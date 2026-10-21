"""Core-only, revocable local case inspection. Never a repair capability.

No plugin export, serialization, logging, network or source mutation. Private
labels/proofs are produced only for the local UI while the active scope holds.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .library_audit_broker import _run_scoped_audit, _unavailable
from .library_inspection_cache import take_inspection_cache

PROVIDERS = {'all': 'Tous les fournisseurs', 'kobo': 'Kobo', 'numilog': 'Numilog',
             'paris': 'Paris', 'local': 'Local', 'unattributed': 'Non attribué', 'other': 'Autre'}
STATES = {
    'all': 'Tous les états', 'confirmed_link': 'Lien confirmé',
    'present_unlinked': 'Présent sans lien confirmé', 'metadata_candidate': 'Candidat bibliographique',
    'ambiguous': 'Correspondance ambiguë', 'hash_mismatch': 'Empreinte différente',
    'rights_without_verified_file': 'Droit sans fichier vérifié',
    'local_file_without_attested_right': 'Fichier sans droit attesté',
    'unverified_file': 'Fichier non vérifié', 'stored_path_missing': 'Chemin enregistré absent',
    'stored_path_unavailable': 'Chemin non inspectable', 'stored_path_out_of_scope': 'Chemin hors périmètre',
}
_REASONS = {
    'artifact_sha_size_candidate': 'Même SHA et taille : mêmes octets, sans preuve de droit.',
    'backup_sha_size_candidate': 'Sauvegarde avec même SHA et taille : provenance à vérifier.',
    'explicit_placement_sha_size': 'Placement explicite avec SHA et taille concordants.',
    'explicit_right_ledger_placement_sha_size': 'Droit, acquisition complète et placement concordants.',
}
_FIELDS = {'entitlement_id': 'Droit', 'provider_id': 'Source déclarée', 'account_id': 'Compte',
           'credential_id': 'Connexion', 'item_id': 'Édition fournisseur', 'state': 'État enregistré',
           'entitlement_type': 'Type de droit', 'attested_source': 'Provenance attestée',
           'artifact_id': 'Artefact', 'sha256': 'SHA enregistré', 'size': 'Taille enregistrée',
           'path': 'Chemin enregistré', 'placement_id': 'Placement', 'status': 'Statut enregistré',
           'catalog_entry_id': 'Notice', 'source_item_id': 'Édition du placement'}


def _text(value):
    if type(value) not in (str, int):
        return ''
    value = str(value)
    value = ''.join(c if c.isprintable() or c == '\n' else '�' for c in value[:2048])
    return value + ('… [texte abrégé]' if len(str(value)) >= 2048 else '')


def _provider(value):
    return value if value in ('kobo', 'numilog', 'paris', 'local') else 'other'


@dataclass(frozen=True, repr=False)
class LocalCase:
    key: str
    label: str
    provider: str
    states: tuple[str, ...]
    details: str


@dataclass(frozen=True, repr=False)
class LocalPage:
    cases: tuple[LocalCase, ...]
    total: int
    offset: int
    limited: bool


class LocalInspection:
    """One active identity, bounded private projection, revocation on every read."""

    def __init__(self, audit, valid, identity_id):
        self._valid = valid
        self._cases = {}
        self._summary = audit.redacted()
        self._limited = False
        self._bytes = 0
        ambiguous = set(audit.metadata_candidates.ambiguous_files)
        edges, candidates, ledgers = defaultdict(list), defaultdict(list), defaultdict(list)
        rights = audit.snapshot.tables.get('entitlements', [])
        catalog = audit.snapshot.tables.get('catalog_entries', [])
        for index, row in enumerate(audit.snapshot.tables.get("acquisition_ledger", [])):
            ledgers[row.get("entitlement_id")].append(index)
        for edge in audit.reconciliation.edges:
            edges[edge.file_index].append(edge)
        for candidate in audit.metadata_candidates.candidates:
            candidates[candidate.file_index].append(candidate)

        def evidence(table, index):
            rows = audit.snapshot.tables.get(table, [])
            if not 0 <= index < len(rows):
                return 'Preuve non disponible.'
            row = rows[index]
            if table != 'artifacts' and row.get('identity_id') != identity_id:
                return 'Preuve hors identité active : détail non divulgué.'
            values = [f'{label} : {_text(row[name])}' for name, label in _FIELDS.items() if row.get(name) is not None]
            if table == 'catalog_entries':
                values.extend(f'Identifiant notice ({_text(scheme)}) : {_text(value)}'
                              for scheme, value in row.get('bibliographic_identifiers', ())[:64])
            return '\n'.join(values)

        def add(kind, index, label, provider, states, details):
            if not self.valid():
                return False
            if len(self._cases) >= 10000:
                self._limited = True
                return False
            details = details[:32768] + ('\nDétail abrégé.' if len(details) > 32768 else '')
            self._bytes += len(details.encode('utf-8')) + len(_text(label).encode('utf-8'))
            if self._bytes > 8*1024*1024:
                self._limited = True
                return False
            key = f'{kind}:{index}'
            self._cases[key] = LocalCase(key, _text(label), provider,
                                        tuple(sorted(set(states) & (STATES.keys() - {'all'}))),
                                        details)
            return True

        for index, observation in enumerate(audit.inventory.files):
            if observation.identity_id != identity_id:
                continue
            states = set(audit.reconciliation.files.get(index, {'unverified_file'}))
            if candidates[index]:
                states.add('metadata_candidate')
            if index in ambiguous:
                states.add('ambiguous')
            providers = {_provider(rights[e.row_index].get('provider_id')) for e in edges[index]
                         if e.table == 'entitlements' and e.certainty == 'attested'
                         and 0 <= e.row_index < len(rights) and rights[e.row_index].get('identity_id') == identity_id}
            provider = next(iter(providers)) if 'confirmed_link' in states and len(providers) == 1 else 'unattributed'
            details = [f'Fichier local : {_text(observation.path)}',
                       f'Taille : {observation.size}', f'Format : {_text(observation.media_format)}',
                       f'SHA calculé : {_text(observation.sha256) or "Non calculé"}',
                       f'Lecture métadonnées : {_text(observation.validation)}',
                       f'Contrôle structure : {_text(observation.structure_validation)}']
            for name, label in (('title', 'Titre OPF'), ('authors', 'Auteurs OPF'), ('publisher', 'Éditeur OPF')):
                value = observation.metadata.get(name)
                if type(value) is list:
                    value = ', '.join(_text(v) for v in value[:16])
                if value:
                    details.append(f'{label} : {_text(value)}')
            from .library_metadata_candidates import identifiers
            details.extend(f'Identifiant OPF ({_text(scheme)}) : {_text(value)}'
                           for scheme, value in identifiers(observation.metadata))
            for edge in edges[index][:20]:
                details.append(_REASONS.get(edge.reason, 'Correspondance non interprétée.') + '\n'
                               + evidence(edge.table, edge.row_index))
                if edge.table == 'entitlements' and 0 <= edge.row_index < len(rights):
                    details.extend('Acquisition enregistrée :\n'+evidence('acquisition_ledger', li)
                                   for li in ledgers[rights[edge.row_index].get('entitlement_id')][:20])
            for candidate in candidates[index][:20]:
                if 0 <= candidate.catalog_row_index < len(catalog):
                    details.append('Identifiant bibliographique explicite identique : candidat uniquement, aucun droit prouvé.\n'
                                   + evidence('catalog_entries', candidate.catalog_row_index))
            if len(edges[index]) > 20 or len(candidates[index]) > 20:
                details.append('Affichage limité à 20 preuves de chaque type.')
            details.append('Constat daté de cette analyse ; revalidation nécessaire avant toute action. Aucun rattachement autorisé.')
            if not add('file', index, Path(observation.path).name, provider, states, '\n\n'.join(details)):
                break
        for index in audit.reconciliation.unverified_rights:
            if not 0 <= index < len(rights) or rights[index].get('identity_id') != identity_id:
                continue
            right = rights[index]
            if not add('right', index, 'Droit sans fichier vérifié · '+_text(right.get('item_id')),
                       _provider(right.get('provider_id')), {'rights_without_verified_file'},
                       evidence('entitlements', index)+'\n\n'+'\n\n'.join(
                           'Acquisition enregistrée :\n'+evidence('acquisition_ledger', li)
                           for li in ledgers[right.get('entitlement_id')][:20])+'\n\nAbsence de preuve de fichier, pas preuve de livre absent. Aucun téléchargement autorisé.'):
                break
        placements = audit.snapshot.tables.get('placements', [])
        for index, state in audit.stored_paths.states.items():
            if state not in STATES or not 0 <= index < len(placements) or placements[index].get('identity_id') != identity_id:
                continue
            # An unverified recorded path cannot attribute a file to a provider.
            if not add('path', index, STATES[state]+' · '+_text(placements[index].get('placement_id')),
                       'unattributed', {state}, evidence('placements', index)):
                break
        if not self.valid():
            self.close()

    def __repr__(self):
        return '<LocalInspection private>'

    def valid(self):
        try:
            valid = self._valid()
        except Exception:
            valid = False
        if not valid:
            self.close()
        return bool(valid)

    def close(self):
        self._cases.clear()
        self._summary = {}
        self._valid = lambda: False

    def summary(self):
        if not self.valid():
            return {}
        return {**self._summary, 'inspection_limited': self._limited}

    def page(self, *, provider='all', state='all', offset=0, limit=50):
        if provider not in PROVIDERS or state not in STATES or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('invalid_inspection_page')
        if not self.valid():
            return LocalPage((), 0, 0, False)
        rows = [case for case in self._cases.values() if (provider == 'all' or case.provider == provider)
                and (state == 'all' or state in case.states)]
        return LocalPage(tuple(rows[offset:offset+limit]), len(rows), offset, self._limited)

    def detail(self, key):
        if not self.valid() or type(key) is not str:
            return None
        return self._cases.get(key)


def open_local_inspection(service, *, identity_id, control=None):
    """Core UI only: consume a fresh scoped projection, otherwise audit again.

    The private projection is never obtained from plugin/public payloads. It
    expires, is single-use, and rechecks live scope and SQLite file stamps on
    every read. It conveys no authorization for a repair or acquisition.
    """
    if control is not None and control.cancelled():
        return _unavailable({'audit_cancelled'})
    cached = take_inspection_cache(service, identity_id)
    if cached is not None:
        return cached
    return _run_scoped_audit(service, identity_id, control,
                             lambda audit, valid: LocalInspection(audit, valid, identity_id))
