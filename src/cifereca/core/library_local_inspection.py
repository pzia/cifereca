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
from .library_manual_link_preflight import review_local_pair, review_source_read_only, review_kobo_candidate

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
    'stored_path_missing_active': 'Chemin absent, placement encore actif',
    'stored_path_missing_historical': 'Ancien emplacement déjà marqué manquant',
    'stored_path_missing_other': 'Chemin absent, état SQLite atypique',
    'loan_inactive': 'Prêt Paris non actif enregistré (lecture seule)',
    'stored_path_unavailable': 'Chemin non inspectable', 'stored_path_out_of_scope': 'Chemin hors périmètre',
}
_REASONS = {
    'artifact_sha_size_candidate': 'Même SHA et taille : mêmes octets, sans preuve de droit.',
    'backup_sha_size_candidate': 'Sauvegarde avec même SHA et taille : provenance à vérifier.',
    'explicit_placement_sha_size': 'Placement explicite avec SHA et taille concordants.',
    'explicit_right_ledger_placement_sha_size': 'Droit, acquisition complète et placement concordants.',
}
_INVENTORY_LIMIT_LABELS = {
    'entry_budget': 'Nombre maximal d’entrées de répertoires atteint.',
    'file_budget': 'Nombre maximal de fichiers observables atteint.',
    'hash_byte_budget': 'Budget global des octets SHA épuisé.',
    'file_byte_budget': 'Au moins un fichier dépasse le plafond unitaire de lecture.',
    'metadata_byte_budget': 'Budget de métadonnées EPUB épuisé.',
    'time_budget': 'Durée limite de l’inventaire atteinte.',
    'depth_budget': 'Profondeur maximale de sous-dossiers atteinte.',
    'entry_unavailable': 'Au moins une entrée de répertoire inaccessible.',
    'directory_unavailable': 'Au moins un dossier ou une racine inaccessible.',
    'directory_changed': 'Un dossier a changé pendant l’inspection.',
    'root_changed': 'Une racine a changé pendant l’inspection.',
    'file_changed': 'Un fichier a changé pendant son inspection.',
    'epub_parser_limit_or_invalid': 'Au moins un EPUB illisible ou hors limites du parseur.',
    'structure_limit_or_invalid': 'Contrôle de structure non concluant pour au moins un fichier.',
    'nofollow_unavailable': 'Contrôles de liens symboliques indisponibles.',
    'cancelled': 'Inventaire interrompu avant la fin.',
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



# #2841: only these codes are strictly PER-OBSERVATION failures. They
# may originate from *other* EPUBs, without invalidating exact, independently
# verified selected-file bytes or the full SQLite proof graph. Never add
# traversal, file/row/byte budgets, root changes or cancellation here.
_PAIR_LOCAL_FILE_REASONS = frozenset({
    'epub_parser_limit_or_invalid',
    'structure_limit_or_invalid',
    'file_byte_budget',  # per-file cap only, not hash/structure global cap
})
# The structure umbrella reason also covers an exhausted shared byte budget.
# A bounded individual invalid ZIP/CRC is non-authoritative for OTHER files,
# but an exhausted global budget MUST NOT become a pair-write permit.
_PAIR_STRUCTURE_BUDGET_STATES = frozenset({
    'structure_byte_or_time_budget',
    'structure_byte_or_entry_budget',
    'structure_cancelled',
})


def _pair_evidence_complete(audit):
    """Strict read-only permission to attempt fresh per-pair validation.

    NOT a write authority: Numilog/Kobo engines independently revalidate the
    entire right/receipt/backup, identity, conflict set and local bytes under
    the Core transaction lock. Metadata suggestion coverage is not ownership
    evidence. Uncertain traversal, incomplete SQLite/WAL, an unfinished proof
    comparison, or unverified path coverage must still block all pairs.
    """
    try:
        inventory = audit.inventory
        snapshot = audit.snapshot
        reconciliation = audit.reconciliation
        if (type(inventory.reasons) is not set
                or type(snapshot.reasons) is not set
                or type(reconciliation.reasons) is not set
                or type(audit.reasons) is not set
                or type(audit.stored_paths.reasons) is not set):
            return False
        if (audit.reasons or snapshot.reasons or audit.stored_paths.reasons
                or not inventory.reasons <= _PAIR_LOCAL_FILE_REASONS
                or reconciliation.reasons - inventory.reasons):
            return False
        if (type(inventory.files_started) is not int
                or inventory.files_started != len(inventory.files)
                or any(file.structure_validation in _PAIR_STRUCTURE_BUDGET_STATES
                       for file in inventory.files)):
            return False
        required = (
            'entitlements', 'acquisition_ledger', 'artifacts',
            'placements', 'backup_items', 'catalog_entries',
        )
        return all(type(snapshot.tables.get(table)) is list for table in required)
    except (AttributeError, TypeError, ValueError):
        return False


def _right_bibliography(right, matches):
    """Private, descriptive only. Identity/provider/edition was matched above.

    A catalogue row is not account-owned evidence; right metadata is held
    inside the already scoped entitlement. Never infer from ISBN, title, SHA
    or a foreign or ambiguous catalogue row.
    """
    notice = matches[0][1] if len(matches) == 1 else {}
    if not matches:
        catalogue_status = 'Aucune notice locale de cette édition exacte.'
    elif len(matches) > 1:
        catalogue_status = (
            'Plusieurs notices locales pour la même édition : '
            'leurs métadonnées ne sont pas attribuées à ce droit.'
        )
    else:
        catalogue_status = (
            'Une notice locale de même identité, fournisseur et édition ; '
            'elle ne prouve pas le compte ni un fichier.'
        )

    # A catalogue record identifies an edition for browsing, not the
    # acquisition/account. When the right itself has an attested, bounded
    # description, that source takes precedence over a conflicting public
    # catalogue label. This is a *display* priority, never a file/right link.
    attested_description = (
        (right.get('provider_id') == 'numilog'
         and right.get('attested_source') == 'numilog.authenticated_library')
        or (right.get('provider_id') == 'kobo'
            and right.get('attested_source') == 'kobo.personal_manifest')
    )

    def field(name, label):
        recorded = right.get(name)
        catalogued = notice.get(name)
        if attested_description and type(recorded) is str and recorded.strip():
            return (_text(recorded).strip()[:256],
                    'bibliothèque authentifiée (description du droit enregistré)')
        if type(catalogued) is str and catalogued.strip():
            return (_text(catalogued).strip()[:256],
                    'notice de catalogue locale de cette édition (non probante)')
        if type(recorded) is str and recorded.strip():
            return (_text(recorded).strip()[:256],
                    'droit enregistré (source descriptive non attestée)')
        return 'Non renseigné', 'Indisponible dans les données locales'

    title, title_source = field('display_title', 'Titre')
    authors, authors_source = field('display_authors', 'Auteur(s)')
    media = right.get('display_media_format')
    media = (media if type(media) is str
             and media in {'epub', 'kepub', 'pdf', 'acsm'} else 'Non renseigné')
    # Provider-private right kind matters for temporary library loans.
    # Neither state="active" nor an expiry string confirms that a Paris
    # loan is currently valid. No ADEPT/license/remote read is performed.
    is_paris_loan = (right.get('provider_id') == 'paris'
                     and right.get('entitlement_type') == 'loan')
    kind = (
        'Prêt temporaire (consultation uniquement)' if is_paris_loan
        else 'Achat / possession enregistrée'
        if right.get('entitlement_type') == 'owned'
        else 'Nature non attestée (consultation uniquement)'
    )
    expiry = _text(right.get('expires_at'))[:96].strip()
    loan_notes = (
        '• Échéance déclarée dans SQLite : ' + (expiry or 'Non renseignée'),
        '• Validité actuelle du prêt / identité ADEPT : non démontrée '
        'par cet enregistrement local. Aucune prolongation, restitution '
        'ou association permanente autorisée.',
    ) if is_paris_loan else ()
    lines = (
        ('Bibliographie du prêt (description, pas preuve de fichier) :'
         if is_paris_loan else
         'Bibliographie de l’achat / droit (description, pas preuve de fichier) :'),
        '• Nature du droit : ' + kind,
        '• Titre : ' + title,
        '• Auteur(s) : ' + authors,
        '• Format déclaré : ' + media,
        '• Fournisseur déclaré : ' + _text(right.get('provider_id')),
        '• Édition fournisseur : ' + (_text(right.get('item_id')) or 'Non renseignée'),
        '• Statut de droit enregistré : ' + (_text(right.get('state')) or 'Indéterminé'),
        *loan_notes,
        '• Origine du titre : ' + title_source,
        '• Origine des auteurs : ' + authors_source,
        '• Catalogue : ' + catalogue_status,
        'Ce bloc ne rattache aucun EPUB ; acquisition, placement, SHA et '
        'structure doivent être contrôlés indépendamment.',
    )
    return (title if title != 'Non renseigné' else None,
            authors if authors != 'Non renseigné' else None,
            '\n'.join(lines))


def _right_explanation(right, ledger, placements, artifacts, files, incomplete,
                       *, conflicting_ledger=False, path_states=None):
    """Explain missing *confirmation*, not physical absence or download status.

    All rows are already restricted to the active identity except shared
    content-addressed artifacts and potentially conflicting ledger entries.
    No metadata, ISBN, filename or SHA alone yields account attribution.
    """
    why = []
    path_states = path_states or {}
    expected = {'kobo': 'kobo.personal_manifest',
                'numilog': 'numilog.authenticated_library'}.get(right.get('provider_id'))
    if not expected or right.get('attested_source') != expected:
        why.append('Provenance du droit insuffisamment attestée dans les données locales.')
    if right.get('entitlement_type') != 'owned' or right.get('state') != 'active':
        why.append('Type ou état du droit ne permet pas de confirmer une acquisition possédée.')
    if conflicting_ledger:
        why.append('Acquisition avec référence de droit identique mais identité, compte, '
                   'connexion ou édition discordants : preuve contradictoire, '
                   'sans divulgation des autres comptes.')
    if not ledger:
        why.append('Aucune acquisition liée à ce droit pour l’identité, le compte, '
                   'la connexion et l’édition exacts dans les preuves SQLite consultées.')
    elif len(ledger) != 1:
        why.append('Plusieurs acquisitions liées au droit dans le même périmètre : '
                   'association ambiguë.')
    elif (ledger[0].get('status') != 'complete'
          or not ledger[0].get('artifact_id')):
        why.append('Acquisition enregistrée sans preuve complète de publication d’artefact.')
    # These are already indexed within the active identity by the caller.
    matched = placements
    if not matched:
        why.append('Aucun placement explicite retrouvé pour cette édition dans les preuves consultées.')
    elif len(matched) != 1:
        why.append('Plusieurs placements de la même édition : vérifier les conflits.')
    else:
        placement = matched[0]
        path = placement.get('path')
        observed = files.get(path, ())
        path_status = path_states.get(id(placement))
        if path_status == 'stored_path_out_of_scope':
            why.append('Chemin enregistré hors des racines autorisées : '
                       'le fichier ne peut pas être vérifié dans cette inspection.')
        elif path_status == 'stored_path_unavailable':
            why.append('Chemin enregistré non inspectable (accès, lien symbolique '
                       'ou vérification interrompue) : présence indéterminée.')
        elif path_status == 'stored_path_missing':
            why.append('Chemin enregistré absent lors du contrôle sécurisé dans '
                       'la racine autorisée ; cela ne prouve pas l’absence d’une autre copie.')
        elif path_status == 'stored_path_present' and not observed:
            why.append('Chemin enregistré présent au contrôle ponctuel, mais fichier '
                       'non observé avec SHA dans l’inventaire borné.')
        if not observed:
            why.append('Aucun fichier correspondant vérifié par SHA à ce chemin '
                       'dans le périmètre inspecté.')
        else:
            if path_status == 'stored_path_missing':
                why.append('Constats temporels contradictoires : le scan a observé '
                           'un fichier mais le contrôle du chemin l’a trouvé absent.')
            if len(ledger) == 1 and ledger[0].get('artifact_id') and (
                    ledger[0].get('artifact_id') != placement.get('artifact_id')):
                why.append('Artefact de l’acquisition différent de celui du placement '
                           ': la chaîne de preuve n’est pas cohérente.')
            linked_artifacts = artifacts.get(placement.get('artifact_id'), ())
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

    def __init__(self, audit, valid, identity_id, roots=(), service=None):
        self._valid = valid
        # Core session only; never serialised or exposed through a plugin.
        self._manual_service = service
        self._manual_identity = identity_id
        self._manual_pending = None
        self._manual_undo = None
        self._manual_undo_pending = None
        # Optional, one-time private cache guard installed only on a verified
        # handoff. A confirmed Core commit may advance its SQLite stamp ONCE
        # so the same-session undo remains reachable.
        self._accept_own_core_commit = None
        self._manual_preview_generation = 0
        # Only Core sets this once when reusing the last completed audit.
        # It never changes scan freshness or grants a file/right link.
        self._from_audit_cache = False
        self._cases = {}
        self._path_missing_by_sqlite_state = Counter()
        # These references exist solely inside this revocable Core UI session.
        # They never enter an audit payload or a future repair capability.
        self._candidate_file_keys = {}
        # Revocable, private evidence summaries; no raw account/path exported.
        self._manual_right_proofs = {}
        self._kobo_right_proofs = {}
        self._kobo_right_scopes = {}
        self._manual_file_proofs = {}
        self._manual_right_scopes = {}
        self._manual_file_observations = {}
        # A declaration is descriptive, never one of the attested strategies.
        self._declared_right_scopes = {}
        self._declared_pending = None
        self._declared_last = None
        self._manual_audit_complete = audit.redacted().get('status') == 'complete'
        self._manual_pair_evidence_complete = _pair_evidence_complete(audit)
        # Private, same-identity facets for the direct-selection file browser.
        # No file metadata or query is ever exported through summary()/logs.
        self._file_facets = {}
        self._summary = audit.redacted()
        # Distinct evidence universes: active rights and recorded placements
        # must not be counted as if each corresponded to one scanned file.
        # Kept only in this revocable, identity-scoped private inspection.
        active_right_indices = {
            i for i, right in enumerate(audit.snapshot.tables.get('entitlements', ()))
            if right.get('identity_id') == identity_id
            and right.get('state') == 'active'
        }
        self._rights_active_count = len(active_right_indices)
        self._rights_unverified_count = len(
            active_right_indices.intersection(audit.reconciliation.unverified_rights)
        )
        self._placements_registered_count = sum(
            placement.get('identity_id') == identity_id
            for placement in audit.snapshot.tables.get('placements', ())
        )
        # #2734: the main profile counter is NOT a filesystem-file count:
        # it counts distinct existing artifact IDs linked by present SQLite
        # placements. Never infer which EPUB/PDF caused any difference.
        # An incomplete SQLite image gives UNKNOWN, not a misleading zero.
        self._scanned_formats = Counter(
            file.media_format for file in audit.inventory.files
            if file.identity_id == identity_id
        )
        self._sqlite_present_artifact_books = None
        self._sqlite_present_placement_count = None
        if (not audit.snapshot.reasons
                and type(audit.snapshot.tables.get('placements')) is list
                and type(audit.snapshot.tables.get('artifacts')) is list):
            artifact_ids = {
                row.get('artifact_id')
                for row in audit.snapshot.tables['artifacts']
                if type(row.get('artifact_id')) is str and row['artifact_id']
            }
            present = [
                row for row in audit.snapshot.tables['placements']
                if row.get('identity_id') == identity_id
                and row.get('state') == 'present'
            ]
            self._sqlite_present_placement_count = len(present)
            self._sqlite_present_artifact_books = len({
                row.get('artifact_id') for row in present
                if row.get('artifact_id') in artifact_ids
            })
        # Private-only coverage: configured roots are not proof that they were
        # traversed successfully. Never mix these paths into audit.redacted().
        self._roots = tuple(str(path) for path in roots)[:128]
        self._unavailable_directories = tuple(
            _text(path) for owner, path in audit.inventory.unavailable_directories
            if owner == identity_id
        )[:128]
        self._unavailable_count = sum(
            owner == identity_id
            for owner, _path in audit.inventory.unavailable_directories
        )
        self._skipped_symlinks = audit.inventory.skipped_symlinks
        self._skipped_special = audit.inventory.skipped_special
        self._inventory_notes = tuple(
            _INVENTORY_LIMIT_LABELS[code]
            for code in sorted(audit.inventory.reasons)
            if code in _INVENTORY_LIMIT_LABELS
        )
        if audit.inventory.reasons - _INVENTORY_LIMIT_LABELS.keys():
            self._inventory_notes += (
                'Autre limite d’inventaire non détaillée : analyse potentiellement incomplète.',
            )
        self._limited = False
        self._bytes = 0
        ambiguous = set(audit.metadata_candidates.ambiguous_files)
        edges, candidates, ledgers = defaultdict(list), defaultdict(list), defaultdict(list)
        rights = audit.snapshot.tables.get('entitlements', [])
        catalog = audit.snapshot.tables.get('catalog_entries', [])
        # The older Kobo mirror intentionally omits manifest provenance and
        # revision from the generic snapshot. It DOES contain a previously
        # recorded backup SHA/size. Use this only to gate a fresh Kobo preview;
        # the dedicated writer checks the missing account/revision/connection.
        kobo_backups = defaultdict(list)
        for backup in audit.snapshot.tables.get('backup_items', ()):
            if backup.get('provider_id') == 'kobo':
                kobo_backups[(
                    backup.get('credential_id'), backup.get('item_id'),
                )].append(backup)
        # Invert the already bounded, exact, same-identity, metadata_only
        # candidates from the shared auditor. There is deliberately NO
        # title/author/path/provider inference at the file level.
        candidate_files_by_notice = defaultdict(set)
        for candidate in audit.metadata_candidates.candidates[:10000]:
            if (candidate.certainty != 'metadata_only'
                    or not 0 <= candidate.file_index < len(audit.inventory.files)
                    or not 0 <= candidate.catalog_row_index < len(catalog)):
                continue
            observed = audit.inventory.files[candidate.file_index]
            notice = catalog[candidate.catalog_row_index]
            states = audit.reconciliation.files.get(candidate.file_index, set())
            if (observed.identity_id == identity_id
                    and notice.get('identity_id') == identity_id
                    and 'local_file_without_attested_right' in states
                    and 'confirmed_link' not in states):
                candidate_files_by_notice[candidate.catalog_row_index].add(
                    candidate.file_index
                )
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
            # Closed local facets, built only from inspected file observations.
            # ISBN and path may help a human find the file, but neither can
            # attach that file to a provider/right/edition.
            authors = observation.metadata.get('authors')
            if type(authors) is str:
                authors = [authors]
            elif type(authors) is not list:
                authors = []
            search_parts = [
                Path(observation.path).name[:260],
                observation.path[-300:],
                observation.metadata.get('title'),
                observation.media_format,
            ] + authors[:8] + [
                value for _scheme, value in identifiers(observation.metadata)[:16]
            ]
            search_text = _searchable(' '.join(
                _text(part)[:256] for part in search_parts if type(part) is str
            ))[:3200]
            self._file_facets[f'file:{index}'] = {
                'format': observation.media_format,
                'sha': bool(observation.sha256),
                'crc': observation.structure_validation == 'epub_structure_crc_ok',
                'search': search_text,
                'name': _searchable(Path(observation.path).name[:260]),
            }
            if 'confirmed_link' not in states:
                self._manual_file_proofs[f'file:{index}'] = {
                    'verified': (
                        observation.media_format == 'epub'
                        and observation.validation == 'ok'
                        and observation.structure_validation == 'epub_structure_crc_ok'
                    ),
                    'signature': (observation.sha256, observation.size),
                }
                # This FileObservation is a private, scanned capability; a
                # later preview still performs its own fresh nofollow SHA.
                self._manual_file_observations[f'file:{index}'] = observation
        placements = audit.snapshot.tables.get('placements', [])
        artifacts = audit.snapshot.tables.get('artifacts', [])
        own_files = [f for f in audit.inventory.files if f.identity_id == identity_id]
        by_path, by_artifact, by_edition = defaultdict(list), defaultdict(list), defaultdict(list)
        for f in own_files:
            by_path[f.path].append(f)
        for artifact in artifacts:
            by_artifact[artifact.get('artifact_id')].append(artifact)
        path_states = {}
        for pi, placement in enumerate(placements):
            if placement.get('identity_id') == identity_id:
                by_edition[(placement.get('provider_id'),
                            placement.get('source_item_id'))].append(placement)
                path_states[id(placement)] = audit.stored_paths.states.get(pi)
        incomplete = audit.redacted().get('status') != 'complete'
        notices = defaultdict(list)
        for notice_index, row in enumerate(catalog):
            if (row.get('identity_id') == identity_id
                    and type(row.get('provider_id')) is str
                    and type(row.get('item_id')) is str and row['item_id']):
                notices[(row['provider_id'], row['item_id'])].append(
                    (notice_index, row)
                )
        # Compute each identity/provider/item candidate set once, not once
        # per unverified right (multiple rights may refer to one edition).
        # The shared metadata_candidates pass is already limited to 10,000
        # exact-key pairs. Its incompleteness is displayed by the audit.
        candidates_by_edition = {}
        for edition, entries in notices.items():
            candidates_by_edition[edition] = tuple(sorted({
                fi for notice_index, _row in entries
                for fi in candidate_files_by_notice.get(notice_index, ())
                if f'file:{fi}' in self._cases
            }))
        def right_scope(row):
            keys = tuple(row.get(name) for name in (
                'identity_id', 'provider_id', 'credential_id',
                'account_id', 'item_id',
            ))
            return keys if all(type(value) is str and value for value in keys) else None

        scope_counts = Counter(
            key for right in rights if (key := right_scope(right)) is not None
        )
        # Active unverified rights remain the audit's original universe.
        # Closed/returned/expired Paris loans are a distinct READ-ONLY
        # inspection population, never counted as active missing rights.
        inactive_paris_loans = {
            index for index, right in enumerate(rights)
            if right.get('identity_id') == identity_id
            and right.get('provider_id') == 'paris'
            and right.get('entitlement_type') == 'loan'
            and right.get('state') != 'active'
        }
        # Preserve active-right priority at the 10,000-case UI memory cap.
        # Historical closed loans cannot hide actionable active rights.
        active_unverified = tuple(dict.fromkeys(audit.reconciliation.unverified_rights))
        active_indices = set(active_unverified)
        displayed_rights = active_unverified + tuple(
            index for index in sorted(inactive_paris_loans)
            if index not in active_indices
        )
        for index in displayed_rights:
            if not 0 <= index < len(rights) or rights[index].get('identity_id') != identity_id:
                continue
            right = rights[index]
            is_closed_paris_loan = index in inactive_paris_loans
            right_provider, right_item = right.get('provider_id'), right.get('item_id')
            matches = (notices.get((right_provider, right_item), [])
                       if type(right_provider) is str and type(right_item) is str
                       and right_item else [])
            title, authors, bibliography = _right_bibliography(right, matches)
            right_label = _display_book(title, authors,
                                        right.get('item_id') or 'Édition non identifiée')
            acquisition_rows = audit.snapshot.tables.get('acquisition_ledger', [])
            # The ledger is intentionally global to detect conflicting rights
            # across accounts. It must NOT, however, become a positive proof
            # for another identity or be displayed in the private right pane.
            ledger_indices = [i for i in ledgers[right.get('entitlement_id')]
                              if 0 <= i < len(acquisition_rows)]
            scope_keys = ('identity_id', 'provider_id', 'credential_id',
                          'account_id', 'item_id')
            own_ledger_indices = [i for i in ledger_indices
                                  if all(acquisition_rows[i].get(key) == right.get(key)
                                         for key in scope_keys)]
            own_ledger = [acquisition_rows[i] for i in own_ledger_indices]
            right_signature = None
            ledger_ok = (len(own_ledger) == 1
                         and own_ledger[0].get('status') == 'complete'
                         and type(own_ledger[0].get('artifact_id')) is str
                         and bool(own_ledger[0]['artifact_id']))
            if ledger_ok:
                matching_artifacts = by_artifact.get(own_ledger[0]['artifact_id'], ())
                if len(matching_artifacts) == 1:
                    right_signature = (
                        matching_artifacts[0].get('sha256'),
                        matching_artifacts[0].get('size'),
                    )
            scope = right_scope(right)
            if (scope is not None and scope[0] == identity_id
                    and scope[1] in ('kobo', 'numilog', 'paris')
                    and scope_counts[scope] == 1):
                self._declared_right_scopes[f'right:{index}'] = scope
            if scope is not None and right.get('provider_id') == 'numilog':
                self._manual_right_scopes[f'right:{index}'] = scope
            # The shared audit does not project backup revision or provider
            # connections. These are deliberately NOT inferred from metadata:
            # the independent Kobo transaction recollects them freshly.
            if right.get('provider_id') == 'kobo':
                backup_rows = (
                    kobo_backups.get((scope[2], scope[4]), ())
                    if scope is not None else ()
                )
                backup = backup_rows[0] if len(backup_rows) == 1 else {}
                backup_sha = backup.get('sha256')
                backup_signature = (
                    (backup_sha.lower(), backup.get('size'))
                    if type(backup_sha) is str else None
                )
                kobo_attested = (
                    scope is not None
                    and len(backup_rows) == 1
                    and backup.get('identity_id') == scope[0]
                    and type(backup_sha) is str and len(backup_sha) == 64
                    and all(ch in '0123456789abcdef' for ch in backup_sha.lower())
                    and type(backup.get('size')) is int and backup['size'] > 0
                    # Legacy backup_items compatibility rights have no
                    # attested_source marker. This gate only permits a fresh
                    # read-only preview, NEVER a write. The Kobo engine
                    # independently requires the personal backup, revision,
                    # active owned entitlement and exact account/SHA.
                    and right.get('state') == 'active'
                    and right.get('entitlement_type') == 'owned'
                    and not right.get('loan_id') and not right.get('expires_at')
                    and scope_counts[scope] == 1
                )
                self._kobo_right_proofs[f'right:{index}'] = {
                    'attested': kobo_attested,
                    'ambiguous': (scope is None or scope_counts[scope] != 1
                                  or len(backup_rows) != 1),
                    'signature': backup_signature,
                }
                if kobo_attested:
                    self._kobo_right_scopes[f'right:{index}'] = scope
            self._manual_right_proofs[f'right:{index}'] = {
                'attested': (
                    right.get('provider_id') == 'numilog'
                    and right.get('attested_source') == 'numilog.authenticated_library'
                    and right.get('state') == 'active'
                    and right.get('entitlement_type') == 'owned'
                    and scope is not None
                ),
                'ambiguous': scope is None or scope_counts[scope] != 1,
                'ledger_conflict': len(own_ledger_indices) != len(ledger_indices),
                'ledger_count': len(own_ledger),
                'ledger_complete': ledger_ok,
                'signature': right_signature,
            }
            why = _right_explanation(
                right, own_ledger, by_edition.get((right_provider, right_item), ()),
                by_artifact, by_path, incomplete,
                conflicting_ledger=len(own_ledger_indices) != len(ledger_indices),
                path_states=path_states,
            )
            # A catalogue notice belongs to this explicit identity/provider/
            # item scope, but an ISBN match between it and an EPUB is only a
            # bibliographic inspection lead, never evidence of ownership.
            candidate_indices = candidates_by_edition.get(
                (right_provider, right_item), (),
            )
            file_keys = tuple(f'file:{fi}' for fi in candidate_indices[:20])
            self._candidate_file_keys[f'right:{index}'] = file_keys
            suggestions = [
                'Fichiers à comparer (même identifiant bibliographique explicite '
                'qu’une notice de cette édition ; candidat non probant, sans '
                'attribution à un fournisseur ni lien de propriété) :',
            ]
            if not candidate_indices:
                suggestions.append(
                    'Aucun candidat bibliographique précis dans les fichiers '
                    'inspectés. Cela ne prouve pas que le livre soit absent.'
                )
            else:
                suggestions.extend(
                    '• ' + self._cases[key].label
                    for key in file_keys
                )
                if len(candidate_indices) > len(file_keys):
                    suggestions.append(
                        f'• {len(candidate_indices)-len(file_keys)} autre(s) '
                        'candidat(s) non affiché(s) (limite 20).'
                    )
                if len(candidate_indices) > 1 or len(matches) > 1:
                    suggestions.append(
                        'Candidats multiples ou notices ambiguës : aucun '
                        'appariement individuel ne peut être conclu.'
                    )
            case_heading = (
                'Prêt Paris non actif (lecture seule)'
                if is_closed_paris_loan else 'Droit sans fichier vérifié'
            )
            case_state = (
                {'loan_inactive'} if is_closed_paris_loan
                else {'rights_without_verified_file'}
            )
            detail = (case_heading + ' · ' + right_label + '\n\n'
                      + bibliography + '\n\n'
                      + evidence('entitlements', index)
                      + '\n\nPourquoi ce droit n’est-il pas confirmé ?\n' + why
                      + '\n\n' + '\n'.join(suggestions)
                      + '\n\n' + '\n\n'.join(
                          'Acquisition enregistrée :\n' + evidence('acquisition_ledger', li)
                          for li in own_ledger_indices[:20])
                      + '\n\nAbsence de preuve de fichier, pas preuve de livre absent. Aucun téléchargement ni rattachement autorisé.')
            if not add('right', index, case_heading + ' · ' + right_label,
                       _provider(right.get('provider_id')),
                       case_state, detail):
                break
        for index, state in audit.stored_paths.states.items():
            if state not in STATES or not 0 <= index < len(placements) or placements[index].get('identity_id') != identity_id:
                continue
            # An old SQLite location is not an active broken link just
            # because its original pathname is absent. Expose the historic
            # state to a private filter, not to any automatic cleanup.
            case_states = {state}
            label = STATES[state]
            sqlite_state = placements[index].get('state')
            if state == 'stored_path_missing':
                bucket = sqlite_state if sqlite_state in ('present', 'missing') else 'other'
                variant = {
                    'present': 'stored_path_missing_active',
                    'missing': 'stored_path_missing_historical',
                    'other': 'stored_path_missing_other',
                }[bucket]
                case_states.add(variant)
                label = STATES[variant]
            if not add('path', index, label+' · '+_text(placements[index].get('placement_id')),
                       'unattributed', case_states, evidence('placements', index)):
                break
            if state == 'stored_path_missing':
                self._path_missing_by_sqlite_state[bucket] += 1
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
        self._from_audit_cache = False
        self._candidate_file_keys.clear()
        self._file_facets.clear()
        self._manual_right_proofs.clear()
        self._kobo_right_proofs.clear()
        self._kobo_right_scopes.clear()
        self._manual_file_proofs.clear()
        self._manual_right_scopes.clear()
        self._manual_file_observations.clear()
        self._declared_right_scopes.clear()
        self._declared_pending = None
        self._declared_last = None
        self._manual_pending = None
        self._manual_undo = None
        self._manual_undo_pending = None
        self._accept_own_core_commit = None
        self._manual_preview_generation += 1
        self._manual_service = None
        self._manual_identity = None
        self._manual_audit_complete = False
        self._manual_pair_evidence_complete = False
        self._summary = {}
        self._path_missing_by_sqlite_state.clear()
        self._scanned_formats.clear()
        self._sqlite_present_artifact_books = None
        self._sqlite_present_placement_count = None
        self._roots = ()
        self._unavailable_directories = ()
        self._unavailable_count = 0
        self._inventory_notes = ()
        self._skipped_symlinks = self._skipped_special = 0
        self._valid = lambda: False

    def from_audit_cache(self) -> bool:
        """Closed, private origin label; never expose a source path or stamp."""
        return self.valid() and self._from_audit_cache is True

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
        file_cases = (
            case for case in self._cases.values() if case.key.startswith('file:')
        )
        # This partition is exclusive even when other evidence states overlap.
        file_partition = Counter(
            'attested' if 'confirmed_link' in case.states else
            'unverified' if 'unverified_file' in case.states else
            'without_attested_link'
            for case in file_cases
        )
        return {'files': by_kind['file'], 'rights': by_kind['right'],
                # These are non-additive identity-scoped summaries: disk
                # observations and persisted artifact references have
                # different scopes, freshness and cardinalities.
                'observed_epub_files': self._scanned_formats['epub'],
                'observed_pdf_files': self._scanned_formats['pdf'],
                'sqlite_present_artifact_books': self._sqlite_present_artifact_books,
                'sqlite_present_placements': self._sqlite_present_placement_count,
                'files_attested': file_partition['attested'],
                'files_individually_unverified': file_partition['unverified'],
                'files_without_attested_link': file_partition['without_attested_link'],
                'rights_active': self._rights_active_count,
                'rights_attested': self._rights_active_count - self._rights_unverified_count,
                'rights_unverified_total': self._rights_unverified_count,
                'placements_registered': self._placements_registered_count,
                'paths': by_kind['path'],
                # Checked, identity-scoped and only within the bounded
                # inspection display. Historical missing entries are NOT
                # active broken paths and are NEVER deleted automatically.
                'paths_active_missing': self._path_missing_by_sqlite_state['present'],
                'paths_historical_missing': self._path_missing_by_sqlite_state['missing'],
                'paths_other_missing': self._path_missing_by_sqlite_state['other'],
                'confirmed_files': by_state['confirmed_link'],
                'unattributed_files': sum(case.key.startswith('file:')
                                          and case.provider == 'unattributed'
                                          for case in self._cases.values()),
                'unverified_rights': by_state['rights_without_verified_file'],
                'paris_inactive_loans': by_state['loan_inactive'],
                'local_without_right': by_state['local_file_without_attested_right'],
                'unverified_files': by_state['unverified_file'],
                'inspection_limited': self._limited,
                'roots': self._roots,
                'inventory_limit_notes': self._inventory_notes,
                'unavailable_directories': self._unavailable_directories,
                'unavailable_directories_total': self._unavailable_count,
                'skipped_symlinks': self._skipped_symlinks,
                'skipped_special': self._skipped_special}

    def page(self, *, provider='all', state='all', offset=0, limit=50, query='',
             hide_historical_paths=False):
        if (provider not in PROVIDERS or state not in STATES or
                type(hide_historical_paths) is not bool or
                type(offset) is not int or offset < 0 or type(limit) is not int or
                not 1 <= limit <= 50 or type(query) is not str or len(query) > 160):
            raise ValueError('invalid_inspection_page')
        if not self.valid():
            return LocalPage((), 0, 0, False)
        terms = tuple(_searchable(term) for term in query.split() if term)
        rows = [case for case in self._cases.values()
                if (provider == 'all' or case.provider == provider)
                and (state == 'all' or state in case.states)
                and (not hide_historical_paths
                     or 'stored_path_missing_historical' not in case.states)
                and (not terms or all(word in _searchable(case.label)
                                      for word in terms))]
        return LocalPage(tuple(rows[offset:offset+limit]), len(rows), offset, self._limited)

    def unconfirmed_files(self, *, offset=0, limit=1, query='',
                          media_format='all', sha_state='all',
                          epub_crc='all', sort='scan'):
        """Private, bounded, directly selectable *unlinked* file page.

        A name/ISBN search or exact file selection supplies NO provenance,
        entitlement or repair authority. A partially scanned source cannot
        be represented as exhaustively empty. Same-identity facets vanish on
        close or scope change. No filesystem or SQLite reads occur here.
        """
        if (type(offset) is not int or offset < 0 or
                type(limit) is not int or not 1 <= limit <= 50 or
                type(query) is not str or len(query) > 160 or
                media_format not in ('all', 'epub', 'pdf') or
                sha_state not in ('all', 'calculated', 'not_calculated') or
                epub_crc not in ('all', 'confirmed', 'other') or
                sort not in ('scan', 'title', 'filename')):
            raise ValueError('invalid_inspection_page')
        if not self.valid():
            return LocalPage((), 0, 0, False)
        terms = tuple(_searchable(term) for term in query.split() if term)
        rows = []
        for case in self._cases.values():
            if not case.key.startswith('file:') or 'confirmed_link' in case.states:
                continue
            facets = self._file_facets.get(case.key)
            if facets is None:
                continue
            if media_format != 'all' and facets['format'] != media_format:
                continue
            if sha_state == 'calculated' and not facets['sha']:
                continue
            if sha_state == 'not_calculated' and facets['sha']:
                continue
            if epub_crc == 'confirmed' and not facets['crc']:
                continue
            if epub_crc == 'other' and facets['crc']:
                continue
            if terms and not all(word in facets['search'] for word in terms):
                continue
            rows.append(case)
        if sort == 'title':
            rows.sort(key=lambda case: (_searchable(case.label), case.key))
        elif sort == 'filename':
            rows.sort(key=lambda case: (
                self._file_facets[case.key]['name'], case.key,
            ))
        return LocalPage(tuple(rows[offset:offset+limit]), len(rows),
                         offset, self._limited)

    def preflight_manual_link(self, right_key, file_key):
        """Explain a selected provider right/file pair without IO or authority.

        Only the already scoped, same-session, *unlinked* cases can enter this
        local assessment. No preflight object permits a subsequent SQLite
        mutation: a separate fresh locked transaction is mandatory.
        """
        if (not self.valid() or type(right_key) is not str
                or type(file_key) is not str
                or len(right_key) > 128 or len(file_key) > 128):
            return review_local_pair(None, None, audit_complete=False)
        right = self._cases.get(right_key)
        file = self._cases.get(file_key)
        if (right is None or file is None
                or not (
                    'rights_without_verified_file' in right.states
                    or (right.provider == 'paris' and 'loan_inactive' in right.states)
                )
                or 'confirmed_link' in file.states
                or not right_key.startswith('right:')
                or not file_key.startswith('file:')):
            return review_local_pair(None, None, audit_complete=False)

        # Shared browsing is available for Kobo and Paris, but neither can
        # accidentally enter Numilog's purchase-only transaction from here.
        # An explicit reason is safer than reporting a real Kobo/Paris right
        # as a missing Numilog entitlement.
        if right.provider == 'kobo':
            proof = self._kobo_right_proofs.get(right_key)
            if not proof or proof.get('attested') is not True:
                return review_source_read_only(
                    'kobo', audit_complete=self._manual_audit_complete,
                    bibliographic_hint=(
                        file_key in self._candidate_file_keys.get(right_key, ())
                    ),
                )
            observation = self._manual_file_observations.get(file_key)
            verified = (
                observation is not None
                and observation.validation == 'ok'
                and observation.media_format in ('epub', 'pdf')
                and (observation.media_format == 'pdf'
                     or observation.structure_validation == 'epub_structure_crc_ok')
            )
            return review_kobo_candidate(
                proof,
                {'verified': verified,
                 'signature': (observation.sha256, observation.size)
                              if observation is not None else None},
                audit_complete=self._manual_pair_evidence_complete,
                global_audit_partial=not self._manual_audit_complete,
                bibliographic_hint=(
                    file_key in self._candidate_file_keys.get(right_key, ())
                ),
            )
        if right.provider != 'numilog':
            return review_source_read_only(
                right.provider,
                audit_complete=self._manual_audit_complete,
                loan_inactive=(right.provider == 'paris'
                               and 'loan_inactive' in right.states),
                bibliographic_hint=(
                    file_key in self._candidate_file_keys.get(right_key, ())
                ),
            )
        return review_local_pair(
            self._manual_right_proofs.get(right_key),
            self._manual_file_proofs.get(file_key),
            audit_complete=self._manual_pair_evidence_complete,
            global_audit_partial=not self._manual_audit_complete,
            bibliographic_hint=(
                file_key in self._candidate_file_keys.get(right_key, ())
            ),
        )

    def revoke_manual_attachment_preview(self):
        """A changed selection invalidates pending and in-flight previews."""
        self._manual_preview_generation += 1
        self._manual_pending = None
        self._manual_undo_pending = None
        self._declared_pending = None

    def _declaration_inputs(self, right_key, file_key):
        if (not self.valid() or self._manual_service is None
                or type(right_key) is not str or type(file_key) is not str
                or not right_key.startswith('right:') or not file_key.startswith('file:')
                or right_key not in self._cases or file_key not in self._cases
                or 'confirmed_link' in self._cases[file_key].states):
            return None
        scope = self._declared_right_scopes.get(right_key)
        observation = self._manual_file_observations.get(file_key)
        if scope is None or observation is None or scope[0] != self._manual_identity:
            return None
        return {'observation': observation, **dict(zip((
            'identity_id', 'provider_id', 'credential_id', 'account_id', 'item_id',
        ), scope))}

    def preview_declared_link(self, right_key, file_key, *, revoke=False,
                              cancelled=lambda: False):
        """Review a descriptive association; no attested fallback or token in GTK.

        Explicitly choosing this action never invents a receipt. All collisions
        and freshness are checked by the independent writer even on a partial
        audit. A selection change cancels both declaration and attested plans.
        """
        self.revoke_manual_attachment_preview()
        generation = self._manual_preview_generation
        options = self._declaration_inputs(right_key, file_key)
        if options is None or type(revoke) is not bool:
            return {'status': 'unavailable', 'read_only': True}
        relation = None
        if revoke:
            last = self._declared_last
            if last is None or last[:2] != (right_key, file_key):
                return {'status': 'unavailable', 'read_only': True}
            relation = last[2]
        from .library_declared_links import (
            DeclarationRejected, declare_local_link, inspect_declared_link,
        )
        try:
            if not revoke:
                try:
                    existing = inspect_declared_link(
                        self._manual_service, **options,
                        cancelled=lambda: (cancelled() or not self.valid()
                                           or generation != self._manual_preview_generation),
                    )
                except DeclarationRejected as error:
                    if str(error) != 'relation_scope_changed':
                        raise
                else:
                    if (not self.valid() or cancelled()
                            or generation != self._manual_preview_generation):
                        return {'status': 'unavailable', 'read_only': True}
                    self._declared_last = (right_key, file_key, existing.relation_id)
                    return {'status': ('needs_review' if existing.status == 'needs_review'
                                       else 'already_declared'),
                            'read_only': True, 'trust': 'user_declaration',
                            'revoke_available': True}
            result = declare_local_link(
                self._manual_service, **options, revoke=relation,
                cancelled=lambda: (cancelled() or not self.valid()
                                   or generation != self._manual_preview_generation),
            )
        except DeclarationRejected:
            return {'status': 'blocked', 'read_only': True}
        if (not self.valid() or cancelled()
                or generation != self._manual_preview_generation):
            return {'status': 'unavailable', 'read_only': True}
        if result.status in ('preview', 'revoke_preview'):
            self._declared_pending = (
                right_key, file_key, result.plan_token, relation,
            )
            # The selected right and file already supply the visible private
            # coordinates. Only the exact fixed mutation is returned here.
            return {'status': result.status, 'read_only': True,
                    'requires_explicit_confirmation': True,
                    'trust': 'user_declaration',
                    'change': 'revoke_declaration' if revoke else 'record_declaration',
                    'rights_mutations': 0, 'placement_mutations': 0,
                    'file_mutations': 0}
        if result.status == 'already_declared':
            self._declared_last = (right_key, file_key, result.relation_id)
            return {'status': 'already_declared', 'read_only': True,
                    'trust': 'user_declaration', 'revoke_available': True}
        return {'status': 'unavailable', 'read_only': True}

    def confirm_declared_link(self, right_key, file_key, *, user_confirmed=False,
                              cancelled=lambda: False):
        """Consume one private declaration plan after a separate human action."""
        pending, self._declared_pending = self._declared_pending, None
        generation = self._manual_preview_generation
        if (user_confirmed is not True or pending is None
                or pending[:2] != (right_key, file_key)):
            return {'status': 'unavailable', 'read_only': True}
        options = self._declaration_inputs(right_key, file_key)
        if options is None:
            return {'status': 'unavailable', 'read_only': True}
        from .library_declared_links import DeclarationRejected, declare_local_link
        acknowledged = [self._accept_own_core_commit is None]

        def committed_under_lock():
            callback = self._accept_own_core_commit
            if callable(callback):
                acknowledged[0] = callback() is True

        try:
            result = declare_local_link(
                self._manual_service, **options, confirmation=pending[2], revoke=pending[3],
                cancelled=lambda: (cancelled() or not self.valid()
                                   or generation != self._manual_preview_generation),
                on_committed=committed_under_lock,
            )
        except DeclarationRejected:
            return {'status': 'blocked', 'read_only': True}
        if result.status in ('declared', 'already_declared'):
            if acknowledged[0] and self.valid():
                self._declared_last = (right_key, file_key, result.relation_id)
            return {'status': result.status, 'read_only': result.changed == 0,
                    'trust': 'user_declaration',
                    'revoke_available': acknowledged[0] and self._declared_last is not None,
                    'rights_mutations': 0, 'placement_mutations': 0, 'file_mutations': 0}
        if result.status in ('revoked', 'already_revoked'):
            self._declared_last = None
            return {'status': result.status, 'read_only': result.changed == 0,
                    'trust': 'user_declaration', 'revoke_available': False,
                    'rights_mutations': 0, 'placement_mutations': 0, 'file_mutations': 0}
        return {'status': 'unavailable', 'read_only': True}

    def _transaction_inputs(self, right_key, file_key):
        if (not self.valid() or self._manual_service is None
                or type(right_key) is not str or type(file_key) is not str):
            return None
        preliminary = self.preflight_manual_link(right_key, file_key)
        if preliminary.status != 'eligible_for_fresh_validation':
            return None
        right = self._manual_right_scopes.get(right_key)
        file = self._manual_file_observations.get(file_key)
        if right is None or file is None or len(right) != 5:
            return None
        identity_id, provider, credential, account, edition = right
        if (provider != 'numilog' or identity_id != self._manual_identity
                or not all(type(value) is str and value
                           for value in (credential, account, edition))):
            return None
        return {
            'observation': file,
            'identity_id': identity_id,
            'credential_id': credential,
            'account_id': account,
            'item_id': edition,
        }

    def _kobo_transaction_inputs(self, right_key, file_key):
        """Private exact identifiers only; never an authorization by title/ISBN."""
        if (not self.valid() or self._manual_service is None
                or type(right_key) is not str or type(file_key) is not str
                or self._cases.get(right_key) is None
                or self._cases[right_key].provider != 'kobo'
                or self.preflight_manual_link(right_key, file_key).status
                   != 'eligible_for_fresh_validation'):
            return None
        scope = self._kobo_right_scopes.get(right_key)
        observation = self._manual_file_observations.get(file_key)
        if (scope is None or observation is None or len(scope) != 5
                or scope[0] != self._manual_identity or scope[1] != 'kobo'
                or not all(type(value) is str and value for value in scope)
                or type(observation.sha256) is not str
                or len(observation.sha256) != 64
                or any(ch not in '0123456789abcdef' for ch in observation.sha256)
                or type(observation.path) is not str
                or not observation.path.startswith('/')):
            return None
        return {
            'identity_id': scope[0],
            'credential_id': scope[2],
            'account_id': scope[3],
            'item_id': scope[4],
            'expected_sha256': observation.sha256,
            'path': observation.path,
        }

    def preview_manual_attachment(self, right_key, file_key, *, cancelled=lambda: False):
        """Fresh SQL/no-follow SHA preview, kept exclusively inside CoreUI.

        No token is exposed in the return or public audit. A later explicit
        GUI confirmation must call confirm_manual_attachment on this exact
        active session; the token is single-use and held in private memory.
        """
        self.revoke_manual_attachment_preview()
        generation = self._manual_preview_generation
        case = self._cases.get(right_key) if self.valid() else None
        if case is not None and case.provider == 'kobo':
            options = self._kobo_transaction_inputs(right_key, file_key)
            if options is None:
                return {'status': 'unavailable', 'read_only': True}
            from .kobo_local_reconciliation import (
                ReconciliationRejected, reconcile_kobo_local,
            )
            try:
                result = reconcile_kobo_local(
                    self._manual_service, **options,
                    cancelled=lambda: (
                        cancelled() or not self.valid()
                        or self._manual_preview_generation != generation
                    ),
                )
            except ReconciliationRejected:
                return {'status': 'blocked', 'read_only': True}
            if (not self.valid() or cancelled()
                    or self._manual_preview_generation != generation):
                return {'status': 'unavailable', 'read_only': True}
            token = result.get('plan_token')
            if result.get('status') == 'preview' and type(token) is str and len(token) == 64:
                self._manual_pending = (right_key, file_key, token)
                return {'status': 'preview', 'read_only': True,
                        'requires_explicit_confirmation': True}
            return {'status': 'already_linked', 'read_only': True} if (
                result.get('status') == 'already_linked'
            ) else {'status': 'unavailable', 'read_only': True}
        options = self._transaction_inputs(right_key, file_key)
        if options is None:
            return {'status': 'unavailable', 'read_only': True}
        from .numilog_manual_link_transaction import (
            ManualAttachmentRejected, reconcile_numilog_manual,
        )
        try:
            result = reconcile_numilog_manual(
                self._manual_service, **options,
                cancelled=lambda: (cancelled() or not self.valid()
                                   or self._manual_preview_generation != generation),
            )
        except ManualAttachmentRejected:
            return {'status': 'blocked', 'read_only': True}
        if (not self.valid() or cancelled()
                or self._manual_preview_generation != generation):
            return {'status': 'unavailable', 'read_only': True}
        token = result.get('plan_token')
        if (result.get('status') == 'preview'
                and type(token) is str and len(token) == 64):
            self._manual_pending = (right_key, file_key, token)
            return {
                'status': 'preview', 'read_only': True,
                'requires_explicit_confirmation': True,
            }
        return {'status': 'already_linked', 'read_only': True} if (
            result.get('status') == 'already_linked'
        ) else {'status': 'unavailable', 'read_only': True}

    def confirm_manual_attachment(self, right_key, file_key, *,
                                  user_confirmed=False, cancelled=lambda: False):
        """One explicit action; still rechecks all Core evidence under lock."""
        pending, self._manual_pending = self._manual_pending, None
        generation = self._manual_preview_generation
        if (user_confirmed is not True or pending is None
                or pending[:2] != (right_key, file_key)):
            return {'status': 'unavailable', 'read_only': True}
        case = self._cases.get(right_key) if self.valid() else None
        if case is not None and case.provider == 'kobo':
            options = self._kobo_transaction_inputs(right_key, file_key)
            if options is None:
                return {'status': 'unavailable', 'read_only': True}
            from .kobo_local_reconciliation import (
                ReconciliationRejected, reconcile_kobo_local,
            )
            cache_stamp_accepted = [None]

            def committed_under_lock():
                callback = self._accept_own_core_commit
                if callable(callback):
                    cache_stamp_accepted[0] = callback() is True

            try:
                result = reconcile_kobo_local(
                    self._manual_service, **options, confirmation=pending[2],
                    cancelled=lambda: (
                        cancelled() or not self.valid()
                        or self._manual_preview_generation != generation
                    ), on_committed=committed_under_lock,
                )
            except ReconciliationRejected:
                return {'status': 'blocked', 'read_only': True}
            status = result.get('status')
            if status in ('applied', 'already_linked'):
                operation = result.get('operation_id')
                if (status == 'applied' and type(operation) is str
                        and len(operation) == 64
                        and (self._accept_own_core_commit is None
                             or cache_stamp_accepted[0] is True)
                        and self.valid()):
                    self._manual_undo = (right_key, file_key, operation)
                return {
                    'status': status, 'read_only': status != 'applied',
                    'undo_available': status == 'applied'
                                      and self._manual_undo is not None,
                }
            return {'status': 'unavailable', 'read_only': True}
        options = self._transaction_inputs(right_key, file_key)
        if options is None:
            return {'status': 'unavailable', 'read_only': True}
        from .numilog_manual_link_transaction import (
            ManualAttachmentRejected, reconcile_numilog_manual,
        )
        # Capture this specific durable commit's stamp *inside* Core's
        # writer/acquisition locks, not afterwards in the GTK worker: another
        # SQLite writer could otherwise advance the cache stamp in between.
        cache_stamp_accepted = [None]
        def committed_under_lock():
            callback = self._accept_own_core_commit
            if callable(callback):
                cache_stamp_accepted[0] = callback() is True

        try:
            result = reconcile_numilog_manual(
                self._manual_service, **options, confirmation=pending[2],
                cancelled=lambda: (cancelled() or not self.valid()
                                   or self._manual_preview_generation != generation),
                on_committed=committed_under_lock,
            )
        except ManualAttachmentRejected:
            return {'status': 'blocked', 'read_only': True}
        # A committed write must not be reported as a failure merely because
        # GTK detached a moment AFTER SQLite commit. The Core transaction
        # checked generation/cancellation before its own final commit.
        if result.get('status') in ('applied', 'already_linked'):
            operation = result.get('operation_id')
            if (result['status'] == 'applied'
                    and type(operation) is str and len(operation) == 64):
                # A cache-sourced inspection guards the old SQLite/WAL
                # stamp. Our OWN confirmed placement changes that stamp;
                # without this one-time acknowledgement the next valid()
                # would revoke the private undo token before the user can
                # use it. Never acknowledge a rejected/preview-only write.
                allowed = (self._accept_own_core_commit is None
                           or cache_stamp_accepted[0] is True)
                # A foreign modification between transaction return and GUI
                # completion must not be normalized as our own commit. The
                # private session is revoked when the stamp changed again.
                if allowed and self.valid():
                    self._manual_undo = (right_key, file_key, operation)
            return {
                'status': result['status'],
                'read_only': result['status'] != 'applied',
                # Operation reference is kept in private Core session.
                'undo_available': result['status'] == 'applied'
                                  and self._manual_undo is not None,
            }
        return {'status': 'unavailable', 'read_only': True}

    def preview_manual_undo(self, right_key, file_key, *, cancelled=lambda: False):
        """Review undo only for the last operation of THIS live session."""
        self.revoke_manual_attachment_preview()
        generation = self._manual_preview_generation
        previous = self._manual_undo
        if (previous is None or previous[:2] != (right_key, file_key)
                or self._manual_service is None):
            return {'status': 'unavailable', 'read_only': True}
        case = self._cases.get(right_key) if self.valid() else None
        if case is not None and case.provider == 'kobo':
            options = self._kobo_transaction_inputs(right_key, file_key)
            if options is None:
                return {'status': 'unavailable', 'read_only': True}
            from .kobo_local_reconciliation import (
                ReconciliationRejected, reconcile_kobo_local,
            )
            try:
                result = reconcile_kobo_local(
                    self._manual_service, **options,
                    undo_operation=previous[2],
                    cancelled=lambda: (
                        cancelled() or not self.valid()
                        or self._manual_preview_generation != generation
                    ),
                )
            except ReconciliationRejected:
                return {'status': 'blocked', 'read_only': True}
            if (not self.valid() or cancelled()
                    or self._manual_preview_generation != generation):
                return {'status': 'unavailable', 'read_only': True}
            token = result.get('plan_token')
            if result.get('status') == 'preview' and type(token) is str and len(token) == 64:
                self._manual_undo_pending = (right_key, file_key, previous[2], token)
                return {'status': 'undo_preview', 'read_only': True,
                        'requires_explicit_confirmation': True}
            if result.get('status') == 'already_undone':
                self._manual_undo = None
                return {'status': 'already_undone', 'read_only': True}
            return {'status': 'unavailable', 'read_only': True}
        options = self._transaction_inputs(right_key, file_key)
        if options is None:
            return {'status': 'unavailable', 'read_only': True}
        from .numilog_manual_link_transaction import (
            ManualAttachmentRejected, undo_numilog_manual,
        )
        try:
            result = undo_numilog_manual(
                self._manual_service, **options, operation_id=previous[2],
                cancelled=lambda: cancelled() or not self.valid()
                          or self._manual_preview_generation != generation,
            )
        except ManualAttachmentRejected:
            return {'status': 'blocked', 'read_only': True}
        if (not self.valid() or cancelled()
                or self._manual_preview_generation != generation):
            return {'status': 'unavailable', 'read_only': True}
        token = result.get('plan_token')
        if (result.get('status') == 'undo_preview'
                and type(token) is str and len(token) == 64):
            self._manual_undo_pending = (right_key, file_key, previous[2], token)
            return {
                'status': 'undo_preview', 'read_only': True,
                'requires_explicit_confirmation': True,
            }
        if result.get('status') == 'already_undone':
            self._manual_undo = None
            return {'status': 'already_undone', 'read_only': True}
        return {'status': 'unavailable', 'read_only': True}

    def confirm_manual_undo(self, right_key, file_key, *,
                            user_confirmed=False, cancelled=lambda: False):
        """Consume the second token once; Core independently checks the journal."""
        pending, self._manual_undo_pending = self._manual_undo_pending, None
        generation = self._manual_preview_generation
        if (user_confirmed is not True or pending is None
                or pending[:2] != (right_key, file_key)
                or self._manual_undo != pending[:3]):
            return {'status': 'unavailable', 'read_only': True}
        case = self._cases.get(right_key) if self.valid() else None
        if case is not None and case.provider == 'kobo':
            options = self._kobo_transaction_inputs(right_key, file_key)
            if options is None:
                return {'status': 'unavailable', 'read_only': True}
            from .kobo_local_reconciliation import (
                ReconciliationRejected, reconcile_kobo_local,
            )
            try:
                result = reconcile_kobo_local(
                    self._manual_service, **options,
                    undo_operation=pending[2], confirmation=pending[3],
                    cancelled=lambda: (
                        cancelled() or not self.valid()
                        or self._manual_preview_generation != generation
                    ),
                )
            except ReconciliationRejected:
                return {'status': 'blocked', 'read_only': True}
            if result.get('status') in ('undone', 'already_undone'):
                self._manual_undo = None
                return {'status': result['status'],
                        'read_only': result['status'] != 'undone'}
            return {'status': 'unavailable', 'read_only': True}
        options = self._transaction_inputs(right_key, file_key)
        if options is None:
            return {'status': 'unavailable', 'read_only': True}
        from .numilog_manual_link_transaction import (
            ManualAttachmentRejected, undo_numilog_manual,
        )
        try:
            result = undo_numilog_manual(
                self._manual_service, **options,
                operation_id=pending[2], confirmation=pending[3],
                cancelled=lambda: cancelled() or not self.valid()
                          or self._manual_preview_generation != generation,
            )
        except ManualAttachmentRejected:
            return {'status': 'blocked', 'read_only': True}
        if result.get('status') in ('undone', 'already_undone'):
            self._manual_undo = None
            return {
                'status': result['status'], 'read_only': result['status'] != 'undone',
            }
        return {'status': 'unavailable', 'read_only': True}

    def candidate_files(self, right_key):
        """Only local, metadata_only cross-reference hints; never an action plan.

        The selected right and candidate files must still be live in this
        private, same-identity session. The caller must not persist results.
        """
        if not self.valid() or type(right_key) is not str:
            return ()
        case = self._cases.get(right_key)
        if case is None or not right_key.startswith('right:'):
            return ()
        return tuple(self._cases[key]
                     for key in self._candidate_file_keys.get(right_key, ())
                     if key in self._cases)

    def detail(self, key):
        if not self.valid() or type(key) is not str:
            return None
        return self._cases.get(key)


def open_local_inspection(service, *, identity_id, control=None, on_mode=None):
    """Core UI only: consume a fresh scoped projection, otherwise audit again.

    The private projection is never obtained from plugin/public payloads. It
    expires, is single-use, and rechecks live scope and SQLite file stamps on
    every read. It conveys no authorization for a repair or acquisition.
    """
    if control is not None and control.cancelled():
        return _unavailable({'audit_cancelled'})
    cached = take_inspection_cache(service, identity_id)

    def publish_mode(code):
        # Core-only, fixed vocabulary; never disclose the identity, cache
        # source stamp, paths, SQLite records or private book information.
        if on_mode is not None:
            try:
                on_mode(code)
            except Exception:
                # Presentation callback cannot make a valid cache unusable.
                pass

    if cached is not None:
        cached._from_audit_cache = True
        publish_mode('cached')
        return cached
    publish_mode('new_scan')
    from .library_audit_roots import configured_identity_roots

    def finish(audit, valid):
        roots = configured_identity_roots(service.config, identity_id)
        return LocalInspection(audit, valid, identity_id,
                               roots=roots.get(identity_id, ()), service=service)

    return _run_scoped_audit(service, identity_id, control, finish)
