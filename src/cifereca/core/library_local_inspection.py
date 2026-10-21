"""Core-only, revocable local case inspection. Never a repair capability.

No plugin export, serialization, logging, network or source mutation. Private
labels/proofs are produced only for the local UI while the active scope holds.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
import unicodedata

from .library_audit_broker import _run_scoped_audit, _unavailable
from .library_inspection_cache import take_inspection_cache

PROVIDERS = {'all': 'Tous les fournisseurs', 'kobo': 'Kobo', 'numilog': 'Numilog',
             'paris': 'Paris', 'local': 'Local (origine attestée)',
             'unattributed': 'Fichiers sans provenance attestée', 'other': 'Autre'}
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


def _display_book(title, authors, fallback):
    """Human label from bounded local metadata, never an ownership claim."""
    title = _text(title)[:160].strip() if type(title) is str else ''
    if type(authors) is list:
        authors = ', '.join(_text(a)[:80] for a in authors[:3] if type(a) is str)
    authors = _text(authors)[:120].strip() if type(authors) is str else ''
    if not title:
        return _text(fallback)
    return title + (' — ' + authors if authors else '') + ' · ' + _text(fallback)[:100]


def _searchable(text):
    """Only local, short, case/accent insensitive queries; never log them."""
    return ''.join(ch for ch in unicodedata.normalize('NFKD', text.casefold())
                   if not unicodedata.combining(ch))


def _right_explanation(right, ledger, placements, artifacts, files, incomplete):
    """Explain missing *confirmation*, not physical absence or download status.

    All rows are already restricted to the active identity except shared
    content-addressed artifacts and potentially conflicting ledger entries.
    No metadata, ISBN, filename or SHA alone yields account attribution.
    """
    why = []
    expected = {'kobo': 'kobo.personal_manifest',
                'numilog': 'numilog.authenticated_library'}.get(right.get('provider_id'))
    if not expected or right.get('attested_source') != expected:
        why.append('Provenance du droit insuffisamment attestée dans les données locales.')
    if right.get('entitlement_type') != 'owned' or right.get('state') != 'active':
        why.append('Type ou état du droit ne permet pas de confirmer une acquisition possédée.')
    if not ledger:
        why.append('Aucune acquisition liée à ce droit dans les preuves SQLite consultées.')
    elif len(ledger) != 1:
        why.append('Plusieurs acquisitions liées au droit : association ambiguë.')
    elif (ledger[0].get('status') != 'complete'
          or not ledger[0].get('artifact_id')):
        why.append('Acquisition enregistrée sans preuve complète de publication d’artefact.')
    matched = [p for p in placements if
               p.get('identity_id') == right.get('identity_id') and
               p.get('provider_id') == right.get('provider_id') and
               p.get('source_item_id') == right.get('item_id')]
    if not matched:
        why.append('Aucun placement explicite retrouvé pour cette édition dans les preuves consultées.')
    elif len(matched) != 1:
        why.append('Plusieurs placements de la même édition : vérifier les conflits.')
    else:
        placement = matched[0]
        path = placement.get('path')
        observed = [f for f in files if f.path == path]
        if not observed:
            why.append('Chemin enregistré sans fichier correspondant vérifié dans le périmètre inspecté.')
        else:
            linked_artifacts = [a for a in artifacts
                                if a.get('artifact_id') == placement.get('artifact_id')]
            if len(linked_artifacts) != 1:
                why.append('Artefact associé au placement absent ou ambigu dans SQLite.')
            else:
                artifact = linked_artifacts[0]
                if not any(f.sha256 and f.sha256 == artifact.get('sha256')
                           and f.size == artifact.get('size') for f in observed):
                    why.append('SHA ou taille du fichier observé non concordants ou non vérifiés.')
            if not any(f.validation == 'ok' and
                       f.structure_validation == 'epub_structure_crc_ok' for f in observed):
                why.append('Structure EPUB non validée pour le fichier observé.')
        if placement.get('state') != 'present':
            why.append('État de placement non confirmé comme présent.')
    if incomplete:
        why.append('Audit partiel : d’autres fichiers ou preuves peuvent être hors inspection.')
    if not why:
        why.append('Chaîne de provenance ou d’unicité non confirmée ; détails à comparer manuellement.')
    why.append('Ces observations ne prouvent ni l’absence physique du livre ni son fournisseur.')
    return '\n'.join('• ' + line for line in why)


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

    def __init__(self, audit, valid, identity_id, roots=()):
        self._valid = valid
        self._cases = {}
        self._summary = audit.redacted()
        self._roots = tuple(str(path) for path in roots)[:128]
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
                if type(row.get('display_title')) is str:
                    values.append('Titre de notice : ' + _text(row['display_title']))
                if type(row.get('display_authors')) is str:
                    values.append('Auteurs de notice : ' + _text(row['display_authors']))
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
            label = _display_book(observation.metadata.get('title'),
                                  observation.metadata.get('authors'),
                                  Path(observation.path).name)
            if not add('file', index, label, provider, states, '\n\n'.join(details)):
                break
        placements = audit.snapshot.tables.get('placements', [])
        artifacts = audit.snapshot.tables.get('artifacts', [])
        own_files = [f for f in audit.inventory.files if f.identity_id == identity_id]
        incomplete = audit.redacted().get('status') != 'complete'
        notices = defaultdict(list)
        for row in catalog:
            if row.get('identity_id') == identity_id:
                notices[(row.get('provider_id'), row.get('item_id'))].append(row)
        for index in audit.reconciliation.unverified_rights:
            if not 0 <= index < len(rights) or rights[index].get('identity_id') != identity_id:
                continue
            right = rights[index]
            matches = notices.get((right.get('provider_id'), right.get('item_id')), [])
            notice = matches[0] if len(matches) == 1 else {}
            right_label = _display_book(notice.get('display_title'),
                                        notice.get('display_authors'),
                                        right.get('item_id') or 'Édition non identifiée')
            acquisition_rows = audit.snapshot.tables.get('acquisition_ledger', [])
            own_ledger = [acquisition_rows[i] for i in ledgers[right.get('entitlement_id')][:20]
                          if 0 <= i < len(acquisition_rows)]
            why = _right_explanation(right, own_ledger, placements, artifacts,
                                     own_files, incomplete)
            detail = ('Droit sans fichier vérifié · ' + right_label + '\n\n'
                      + evidence('entitlements', index)
                      + '\n\nPourquoi ce droit n’est-il pas confirmé ?\n' + why
                      + '\n\n' + '\n\n'.join(
                          'Acquisition enregistrée :\n' + evidence('acquisition_ledger', li)
                          for li in ledgers[right.get('entitlement_id')][:20])
                      + '\n\nAucun téléchargement ni rattachement autorisé.')
            if not add('right', index, 'Droit non vérifié · ' + right_label,
                       _provider(right.get('provider_id')),
                       {'rights_without_verified_file'}, detail):
                break
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
        self._roots = ()
        self._valid = lambda: False

    def summary(self):
        if not self.valid():
            return {}
        return {**self._summary, 'inspection_limited': self._limited}

    def overview(self):
        """Local-only counts: file, right and placement are distinct entities.

        States overlap, so state subtotals NEVER purport to sum to all books.
        """
        if not self.valid():
            return {}
        by_kind = Counter(case.key.split(':', 1)[0] for case in self._cases.values())
        by_state = Counter(state for case in self._cases.values()
                           for state in case.states)
        return {'files': by_kind['file'], 'rights': by_kind['right'],
                'paths': by_kind['path'],
                'confirmed_files': by_state['confirmed_link'],
                'unattributed_files': sum(case.key.startswith('file:')
                                          and case.provider == 'unattributed'
                                          for case in self._cases.values()),
                'unverified_rights': by_state['rights_without_verified_file'],
                'local_without_right': by_state['local_file_without_attested_right'],
                'unverified_files': by_state['unverified_file'],
                'inspection_limited': self._limited,
                'roots': self._roots}

    def page(self, *, provider='all', state='all', offset=0, limit=50, query=''):
        if (provider not in PROVIDERS or state not in STATES or
                type(offset) is not int or offset < 0 or type(limit) is not int or
                not 1 <= limit <= 50 or type(query) is not str or len(query) > 160):
            raise ValueError('invalid_inspection_page')
        if not self.valid():
            return LocalPage((), 0, 0, False)
        terms = tuple(_searchable(term) for term in query.split() if term)
        rows = [case for case in self._cases.values()
                if (provider == 'all' or case.provider == provider)
                and (state == 'all' or state in case.states)
                and (not terms or all(word in _searchable(case.label + '\n' + case.details)
                                      for word in terms))]
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
