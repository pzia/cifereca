"""Maintenance entry consuming only the public offline reconciliation broker."""
from collections.abc import Mapping

from cifereca.plugin_api import MaintenanceContext, MaintenancePlan, MaintenanceResult, MaintenanceToolInfo
from cifereca_library_audits import _ReadOnlyTool

_LIMITS = {
    'audit_cancelled': 'Analyse interrompue.',
    'cancelled': 'Analyse interrompue.',
    'snapshot_cancelled': 'Lecture de la base interrompue.',
    'audit_time_budget': 'Durée maximale de l’analyse atteinte.',
    'time_budget': 'Durée maximale de l’inventaire atteinte.',
    'snapshot_time_budget': 'Durée maximale de lecture de la base atteinte.',
    'hash_byte_budget': 'Budget de lecture SHA atteint.',
    'file_byte_budget': 'Au moins un fichier dépasse la taille maximale inspectable.',
    'entry_budget': 'Nombre maximal d’entrées à parcourir atteint.',
    'file_budget': 'Nombre maximal de fichiers à inspecter atteint.',
    'depth_budget': 'Profondeur maximale de parcours atteinte.',
    'directory_unavailable': 'Au moins une racine ou un répertoire reste non inspectable.',
    'entry_unavailable': 'Au moins une entrée reste non inspectable.',
    'root_changed': 'Une racine a changé pendant l’analyse.',
    'directory_changed': 'Un répertoire a changé pendant l’analyse.',
    'file_changed': 'Un fichier a changé pendant l’analyse.',
    'nofollow_unavailable': 'Le parcours sécurisé des fichiers est indisponible sur cette installation.',
    'epub_parser_limit_or_invalid': 'Au moins un EPUB dépasse les limites de lecture ou contient des métadonnées invalides.',
    'structure_limit_or_invalid': 'Au moins un fichier dépasse les limites structurelles ou échoue au contrôle de structure.',
    'metadata_byte_budget': 'Budget mémoire des métadonnées atteint.',
    'comparison_budget': 'Budget de comparaison des preuves atteint.',
    'metadata_candidate_budget_or_cancelled': 'La recherche de correspondances bibliographiques reste incomplète.',
    'metadata_unavailable': 'Certaines métadonnées bibliographiques ne sont pas inspectables.',
    'stored_path_budget': 'Budget de vérification des chemins enregistrés atteint.',
    'stored_path_unavailable': 'Certains chemins enregistrés restent non inspectables.',
    'snapshot_row_budget': 'Nombre maximal de lignes de preuve atteint.',
    'snapshot_schema_incomplete': 'Certaines tables ou colonnes de preuve sont indisponibles.',
    'snapshot_byte_budget_or_invalid': 'La base dépasse la limite de lecture ou n’est pas inspectable.',
    'snapshot_unavailable': 'La base locale n’a pas pu être inspectée.',
    'snapshot_changed': 'La base a changé pendant sa lecture.',
    'snapshot_reader_unavailable': 'La lecture sécurisée de la base est indisponible sur cette installation.',
    'snapshot_wal_reader_unavailable': 'La lecture de cette base active est indisponible sur cette installation.',
    'snapshot_identity_migration': 'Une migration de bibliothèque doit être terminée avant l’analyse.',
    'reconciliation_scope_changed': 'L’identité ou sa configuration a changé : relancez l’analyse.',
    'reconciliation_identity_not_active': 'L’identité sélectionnée n’est plus active : relancez l’analyse.',
    'reconciliation_identity_root_collision': 'Les répertoires configurés de plusieurs identités se chevauchent.',
}


def _limit_warnings(result):
    reasons = result.get('reasons', []) if type(result) is dict else []
    if type(reasons) is not list or len(reasons) > 64:
        return ('La cause de certaines limites n’est pas disponible.',)
    labels = []
    for reason in reasons:
        if type(reason) is str and reason in _LIMITS and _LIMITS[reason] not in labels:
            labels.append(_LIMITS[reason])
        elif type(reason) is not str or reason not in _LIMITS:
            label = 'La cause de certaines limites n’est pas disponible.'
            if label not in labels:
                labels.append(label)
    return tuple(labels)


_COUNTER_LABELS = {
    'observed_files': 'Fichiers observés',
    'hashed_files': 'Fichiers dont le SHA a été calculé',
    'confirmed_link': 'Liens confirmés par les preuves locales',
    'present_unlinked': 'Fichiers présents sans lien confirmé',
    'metadata_candidates': 'Candidats bibliographiques sans preuve de droit',
    'ambiguous': 'Correspondances ambiguës',
    'hash_mismatch': 'Empreintes différentes des preuves enregistrées',
    'rights_without_verified_file': 'Droits sans fichier local vérifié',
    'local_file_without_attested_right': 'Fichiers sans droit local attesté',
    'unverified_file': 'Fichiers restant non vérifiés',
    'stored_path_missing': 'Chemins enregistrés absents au contrôle',
    'stored_path_unavailable': 'Chemins enregistrés non inspectables',
    'stored_path_out_of_scope': 'Chemins enregistrés hors périmètre autorisé',
}


class LibraryReconciliationAuditTool(_ReadOnlyTool):
    info = MaintenanceToolInfo(
        id='library.reconciliation.audit', title='Réconciliation de bibliothèque',
        description='Compare les fichiers locaux et les preuves de bibliothèque, hors réseau et sans réparation.',
        icon_name='view-refresh-symbolic', scope='library', danger_level='read-only',
        supports_dry_run=True, cancellable=True,
    )

    def __init__(self):
        super().__init__()
        self._planned = None

    def activate(self, context):
        self._planned = None
        return super().activate(context)

    def deactivate(self):
        self._planned = None
        super().deactivate()

    def plan(self, context: MaintenanceContext) -> MaintenancePlan:
        # Planning does not scan or call the Core audit. Keep the identity in
        # bounded private tool state, never in preview facts/logs. One live plan
        # per singleton; copied, replaced, stale or cross-tool plans fail closed.
        identity = context.identity_id
        available = type(identity) is str and bool(identity)
        try:
            available = available and callable(getattr(self._broker('library.reconciliation.read'), 'audit', None))
        except Exception:
            available = False
        plan = MaintenancePlan(
            tool_id=self.info.id,
            summary=('Analyser les fichiers et preuves locales de l’identité sélectionnée.'
                     if available else 'Réconciliation indisponible : identité active et Core compatible requis.'),
            warnings=('Lecture seule et hors réseau ; aucun rattachement ni téléchargement.',),
            facts={'schema': 1, 'available': available},
        )
        self._planned = (plan, identity) if available else None
        return plan

    @staticmethod
    def _failure(summary='Réconciliation indisponible : preuves locales non vérifiables.', limits=()):
        return MaintenanceResult(ok=False, changed=0, summary=summary,
                                 warnings=('Aucune réparation ni acquisition effectuée.',)+tuple(limits),
                                 facts={'incomplete_scan': 1})

    def run(self, plan: MaintenancePlan, control) -> MaintenanceResult:
        control.check_cancelled()
        saved, self._planned = self._planned, None
        try:
            valid = (saved is not None and plan is saved[0] and plan.tool_id == self.info.id
                     and type(plan.facts) is dict and len(plan.facts) == 2
                     and type(plan.facts.get('schema')) is int and plan.facts['schema'] == 1
                     and plan.facts.get('available') is True)
        except Exception:
            valid = False
        if not valid:
            return self._failure('Réconciliation non vérifiable : relancez la préparation.')
        try:
            result = self._broker('library.reconciliation.read').audit(identity_id=saved[1], control=control)
        except Exception:
            control.check_cancelled()
            return self._failure()
        control.check_cancelled()
        # Project known numeric counters only. Never echo Core prose, warning
        # text, reasons, unknown fields, groups or any caller-supplied plan text.
        if (type(result) is not dict or type(result.get('schema')) is not int or result.get('schema') != 1
                or result.get('read_only') is not True
                or result.get('acquisition_authorized') is not False
                or result.get('repair_authorized') is not False
                or type(result.get('status')) is not str
                or result.get('status') not in {'complete', 'incomplete_scan'}):
            return self._failure()
        limits = _limit_warnings(result)
        try:
            inventory = result['inventory']
            graph = result['reconciliation']['counts']
            candidates = result['metadata_candidates']
            paths = result['stored_paths']['counts']
            if not all(isinstance(value, Mapping) for value in (inventory, graph, candidates, paths)):
                return self._failure(limits=limits)
            counts = {'observed_files': inventory['files'], 'hashed_files': inventory['hashed_files'],
                      'metadata_candidates': candidates['metadata_candidates']}
            for name in ('confirmed_link', 'present_unlinked', 'ambiguous', 'hash_mismatch',
                         'rights_without_verified_file', 'local_file_without_attested_right', 'unverified_file'):
                counts[name] = graph.get(name, 0)
            for name in ('stored_path_missing', 'stored_path_unavailable', 'stored_path_out_of_scope'):
                counts[name] = paths.get(name, 0)
            if any(type(value) is not int or not 0 <= value <= 1_000_000 for value in counts.values()):
                return self._failure()
        except Exception:
            return self._failure(limits=limits)
        reasons = result.get('reasons', [])
        complete = result['status'] == 'complete' and type(reasons) is list and not reasons
        counts['incomplete_scan'] = int(not complete)
        summary = (f"{counts['observed_files']} fichier(s) observé(s), "
                   f"{counts['confirmed_link']} lien(s) confirmé(s), "
                   f"{counts['metadata_candidates']} correspondance(s) bibliographique(s).")
        warnings = ['Un SHA ou un ISBN ne prouve pas un droit. Aucun rattachement ni téléchargement.']
        if not complete:
            summary = 'Analyse incomplète. '+summary
            warnings.append('Certaines preuves restent non vérifiées ; un droit non vérifié ne signifie pas un livre absent.')
            warnings.extend(limits)
        control.report(message='Réconciliation locale terminée' if complete else 'Réconciliation locale incomplète')
        control.check_cancelled()
        # Additive public presentation capability. Older compatible Core
        # versions still receive the existing summary/warnings/facts result.
        presentation = {}
        if hasattr(MaintenanceResult, 'details'):
            presentation['details'] = tuple(
                f'{label} : {counts[name]}' for name, label in _COUNTER_LABELS.items()
            ) + ('Les catégories peuvent se recouper ; ne pas additionner ces compteurs.',)
        return MaintenanceResult(ok=complete, changed=0, summary=summary,
                                 warnings=tuple(warnings), facts=counts, **presentation)


RECONCILIATION_AUDIT = LibraryReconciliationAuditTool()
