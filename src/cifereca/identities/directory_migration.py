"""Journaled identity directory migration. Originals survive until DB/config commit."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from cifereca.plugin_api import BUNDLE_MANIFEST_NAME
from cifereca.library_files import safe_library_component, sha256_file

JOURNAL_KEY = 'identity-directory-migration-v1'

class DirectoryMigrationError(RuntimeError):
    pass


def checked_tree(path: Path):
    if path.is_symlink():
        raise DirectoryMigrationError(f'Lien symbolique refusé pour la migration : {path}')
    if not path.exists():
        return
    nodes = [path] + list(path.rglob('*')) if path.is_dir() else [path]
    for node in nodes:
        if node.is_symlink() or not (node.is_dir() or node.is_file()):
            raise DirectoryMigrationError(f'Entrée non régulière refusée : {node}')


def fingerprint(path: Path) -> str:
    checked_tree(path)
    if path.is_file():
        return 'file:' + sha256_file(path)
    if not path.is_dir():
        raise DirectoryMigrationError(f'Chemin disparu : {path}')
    digest = hashlib.sha256(b'directory-v1\0')
    for node in sorted(path.rglob('*')):
        digest.update(node.relative_to(path).as_posix().encode() + b'\0')
        digest.update((('file:' + sha256_file(node)) if node.is_file() else 'directory').encode() + b'\0')
    return 'directory:' + digest.hexdigest()


def rewrite_path(value, mappings, root, key=""):
    """Only complete local paths/relative paths are rewritten, never URLs or prose."""
    if isinstance(value, dict):
        return {k: rewrite_path(v, mappings, root, k) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite_path(v, mappings, root, key) for v in value]
    path_key = not key or key in {'path', 'paths', 'files', 'directory', 'destination', 'target', 'staging'} or key.endswith(('_path', '_dir', '_directory'))
    if not path_key or not isinstance(value, str) or '://' in value:
        return value
    for old, new in sorted(mappings, key=lambda row: len(row[0]), reverse=True):
        for source, target in [(old, new), (str(Path(old).relative_to(root)), str(Path(new).relative_to(root)))]:
            if value == source:
                return target
            if value.startswith(source + os.sep):
                return target + value[len(source):]
    return value


class DirectoryMigration:
    def __init__(self, config, storage, root):
        self.config, self.storage = config, storage
        self.root = Path(root).expanduser().resolve()

    def pending(self):
        value = self.storage.metadata_get(JOURNAL_KEY)
        return json.loads(value) if value else None

    def _write(self, plan):
        self.storage.metadata_set(JOURNAL_KEY, json.dumps(plan))

    @staticmethod
    def validate(rows):
        ids, names = set(), set()
        if not isinstance(rows, list) or not rows:
            raise DirectoryMigrationError('Au moins une identité doit être conservée.')
        for row in rows:
            iid = str(row.get('id') or '')
            name = str(row.get('directory_name') or iid)
            if not iid or iid in ids or not re.fullmatch(r'[A-Za-z0-9._-]+', iid):
                raise DirectoryMigrationError('Identifiant d’identité invalide ou dupliqué.')
            if name in names or name in {'.', '..'} or not re.fullmatch(r'[A-Za-z0-9._-]+', name) or safe_library_component(name, iid) != name:
                raise DirectoryMigrationError('Répertoire d’identité invalide ou dupliqué.')
            ids.add(iid); names.add(name)

    def plan(self, rows):
        self.validate(rows)
        normalized = deepcopy(self.config)
        normalized.set('identities', rows)
        rows = normalized.identities()
        old_rows = deepcopy(self.config.identities())
        self.validate(old_rows)
        old = {row['id']: row for row in old_rows}
        owners = {str(row.get('directory_name') or row['id']): row['id'] for row in old_rows}
        changes = []
        for row in rows:
            iid = row['id']; new = str(row.get('directory_name') or iid)
            prior = old.get(iid)
            if not prior:
                if new in owners:
                    raise DirectoryMigrationError('Ce répertoire appartient déjà à une autre identité.')
                continue
            previous = str(prior.get('directory_name') or iid)
            if previous == new:
                continue
            if new in owners and owners[new] != iid:
                raise DirectoryMigrationError('La destination appartient à une autre identité. Les échanges de répertoires se font en étapes distinctes.')
            source, target = self.root / previous, self.root / new
            if source == self.root or target == self.root or source.parent != self.root or target.parent != self.root:
                raise DirectoryMigrationError('La migration exige des sous-répertoires directs de la bibliothèque.')
            checked_tree(source); checked_tree(target)
            if source.exists() and not source.is_dir():
                raise DirectoryMigrationError(f'La source doit être un répertoire : {source}')
            if target.exists() and not target.is_dir():
                raise DirectoryMigrationError(f'La destination doit être un répertoire : {target}')
            changes.append({'identity_id': iid, 'old': str(source), 'new': str(target)})
        # Old scans inferred an identity from an undeclared folder name.
        # Accept only that exact legacy inference, never a known or attested owner.
        known = set(old) | {str(row['identity_id']) for row in self.storage.identities()}
        attested = {str(row.get('identity_id')) for row in self.storage.acquisitions() if row.get('identity_id')}
        attested.update(str(row.get('identity_id')) for row in self.storage.entitlements() if row.get('identity_id'))
        for change in changes:
            for placement in self.storage.placements():
                path = str(placement['path'])
                for side in ('old', 'new'):
                    directory = change[side]
                    if path != directory and not path.startswith(directory + os.sep):
                        continue
                    owner = placement.get('identity_id')
                    inferred = owner == Path(directory).name and owner not in known and owner not in attested
                    if owner not in {None, '', change['identity_id']} and not inferred:
                        raise DirectoryMigrationError('La destination contient des fichiers indexés pour une autre identité.')
        plan = {'version': 1, 'id': uuid.uuid4().hex, 'state': 'planned', 'root': str(self.root),
                'config_path': str(self.config.path.resolve()), 'old_identities': old_rows,
                'new_identities': deepcopy(rows), 'changes': changes, 'entries': [], 'collisions': 0}
        reserved = set()
        def add(source, target):
            digest = fingerprint(source)
            initial = target
            serial = 0
            while str(target) in reserved or (target.exists() and fingerprint(target) != digest):
                serial += 1
                token = digest.split(':', 1)[1][:10]
                suffix = '' if serial == 1 else f'-{serial}'
                extension = '.kepub.epub' if initial.name.lower().endswith('.kepub.epub') else initial.suffix
                stem = initial.name[:-len(extension)] if extension else initial.name
                target = initial.with_name(f'{stem}-migration-{token}{suffix}{extension}')
            if target != initial:
                plan['collisions'] += 1
            reserved.add(str(target))
            plan['entries'].append({'old': str(source), 'new': str(target), 'digest': digest,
                                    'existed': target.exists()})
        def visit(source, target):
            if not source.exists():
                return
            atomic = source.is_dir() and ((source / BUNDLE_MANIFEST_NAME).exists() or any(source.glob('*.m3u')) or any(source.glob('*.m3u8')))
            if source.is_dir() and target.is_dir() and not atomic:
                for child in sorted(source.iterdir()):
                    visit(child, target / child.name)
            else:
                add(source, target)
        for change in changes:
            source = Path(change['old'])
            if source.exists():
                for child in sorted(source.iterdir()):
                    visit(child, Path(change['new']) / child.name)
        return plan

    def _validate_plan(self, plan):
        if not re.fullmatch(r'[0-9a-f]{32}', str(plan.get('id', ''))) or plan.get('state') not in {'planned', 'committed'}:
            raise DirectoryMigrationError('Journal de migration invalide.')
        if plan.get('root') != str(self.root) or plan.get('config_path') != str(self.config.path.resolve()):
            raise DirectoryMigrationError('La bibliothèque ou la configuration a changé pendant une migration. Rétablissez leur emplacement avant la reprise.')
        current = self.config.identities()
        if current not in [plan['old_identities'], plan['new_identities']]:
            raise DirectoryMigrationError('Les identités ont changé pendant une migration ; rétablissez la configuration pour reprendre.')
        for change in plan['changes']:
            for key in ['old', 'new']:
                path = Path(change[key])
                if path.parent != self.root or path.name in {'.', '..'}:
                    raise DirectoryMigrationError('Journal de migration invalide.')
                checked_tree(path)
        for entry in plan['entries']:
            if any('..' in Path(entry[key]).parts for key in ['old', 'new']) or not any(Path(entry['old']).resolve().is_relative_to(Path(c['old'])) and Path(entry['new']).resolve().is_relative_to(Path(c['new'])) for c in plan['changes']):
                raise DirectoryMigrationError('Chemin hors du journal de migration.')

    def _copy_entry(self, entry, stage):
        source, target = Path(entry['old']), Path(entry['new'])
        if target.exists():
            if fingerprint(target) != entry['digest']:
                raise DirectoryMigrationError(f'La destination a changé ; aucun fichier écrasé : {target}')
            return
        if fingerprint(source) != entry['digest']:
            raise DirectoryMigrationError(f'La source a changé ; migration suspendue : {source}')
        if not target.parent.resolve().is_relative_to(self.root):
            raise DirectoryMigrationError('La destination sort de la bibliothèque ; original conservé.')
        target.parent.mkdir(parents=True, exist_ok=True)
        if stage.exists():
            shutil.rmtree(stage) if stage.is_dir() else stage.unlink()
        if source.is_dir():
            shutil.copytree(source, stage)
        else:
            shutil.copy2(source, stage)
        if fingerprint(stage) != entry['digest']:
            raise DirectoryMigrationError('La copie ne correspond pas à la source ; original conservé.')
        if source.is_dir():
            if target.exists():
                raise DirectoryMigrationError('La destination a été créée pendant la copie ; original conservé.')
            os.rename(stage, target)
        else:
            # Atomic publish with exclusive creation; never replace a file.
            os.link(stage, target)
            stage.unlink()

    def run(self, plan=None):
        resuming = plan is None
        plan = plan or self.pending()
        if not plan:
            return None
        self._validate_plan(plan)
        stage_root = self.root / ('.cifereca-migration-' + plan['id'])
        if stage_root.is_symlink():
            raise DirectoryMigrationError('Répertoire temporaire invalide.')
        if plan['state'] == 'planned':
            stage_root.mkdir(parents=True, exist_ok=True)
            for index, entry in enumerate(plan['entries']):
                self._copy_entry(entry, stage_root / str(index))
            for entry in plan['entries']:
                if fingerprint(Path(entry['old'])) != entry['digest'] or fingerprint(Path(entry['new'])) != entry['digest']:
                    raise DirectoryMigrationError('Un fichier a changé avant validation ; originaux conservés.')
            # Reject newly appearing source files instead of abandoning them.
            for change in plan['changes']:
                source = Path(change['old'])
                for path in source.rglob('*') if source.exists() else []:
                    if path.is_file() and not any(path.is_relative_to(Path(e['old'])) for e in plan['entries']):
                        raise DirectoryMigrationError('Nouveau fichier dans la source ; annulez puis relancez la migration.')
            self.storage.commit_directory_migration(plan)
            plan['state'] = 'committed'
        self.config.set('identities', plan['new_identities'])
        self.config.save()
        for entry in plan['entries']:
            source, target = Path(entry['old']), Path(entry['new'])
            if fingerprint(target) != entry['digest']:
                raise DirectoryMigrationError('Le fichier cible a changé ; original conservé pour récupération.')
            if source.exists():
                checked_tree(source)
                if source.is_dir():
                    # A crash may have removed only some bundle members. Check
                    # every remaining file against the committed target.
                    for file in sorted(source.rglob('*')):
                        if file.is_file():
                            counterpart = target / file.relative_to(source)
                            if not counterpart.is_file() or sha256_file(file) != sha256_file(counterpart):
                                raise DirectoryMigrationError('Le fichier source a changé ; il est conservé. Reprise nécessaire.')
                            file.unlink()
                    for directory in sorted((p for p in source.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
                        directory.rmdir()
                    source.rmdir()
                else:
                    if fingerprint(source) != entry['digest']:
                        raise DirectoryMigrationError('Le fichier source a changé ; il est conservé. Reprise nécessaire.')
                    source.unlink()
        for change in plan['changes']:
            source = Path(change['old'])
            if source.exists():
                for directory in sorted((p for p in source.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
                    try: directory.rmdir()
                    except OSError: pass
                try: source.rmdir()
                except OSError:
                    raise DirectoryMigrationError('Des fichiers restent dans l’ancien dossier ; ils sont conservés. Reprise nécessaire.')
            Path(change['new']).mkdir(parents=True, exist_ok=True)
        if stage_root.exists():
            shutil.rmtree(stage_root)
        return {'migrated': len(plan['changes']), 'units': len(plan['entries']), 'collisions': plan['collisions'], 'resumed': resuming}

    def finish(self):
        self.storage.metadata_set(JOURNAL_KEY, '')

    def abort(self):
        plan = self.pending()
        if not plan:
            return {'aborted': False}
        self._validate_plan(plan)
        if plan['state'] != 'planned':
            raise DirectoryMigrationError('Migration déjà validée en base : reprendre la migration, sans annuler les chemins validés.')
        for entry in plan['entries']:
            source, target = Path(entry['old']), Path(entry['new'])
            if not entry['existed'] and target.exists() and source.exists() and fingerprint(source) == entry['digest'] and fingerprint(target) == entry['digest']:
                shutil.rmtree(target) if target.is_dir() else target.unlink()
        stage = self.root / ('.cifereca-migration-' + plan['id'])
        if stage.exists(): shutil.rmtree(stage)
        self.finish()
        return {'aborted': True, 'originals_preserved': True}
