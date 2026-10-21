"""Maintenance entry consuming only the public offline reconciliation broker."""
from collections.abc import Mapping

from cifereca.plugin_api import MaintenanceContext, MaintenancePlan, MaintenanceResult, MaintenanceToolInfo
from cifereca_library_audits import _ReadOnlyTool


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
    def _failure(summary='Réconciliation indisponible : preuves locales non vérifiables.'):
        return MaintenanceResult(ok=False, changed=0, summary=summary,
                                 warnings=('Aucune réparation ni acquisition effectuée.',),
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
        try:
            inventory = result['inventory']
            graph = result['reconciliation']['counts']
            candidates = result['metadata_candidates']
            paths = result['stored_paths']['counts']
            if not all(isinstance(value, Mapping) for value in (inventory, graph, candidates, paths)):
                return self._failure()
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
            return self._failure()
        complete = result['status'] == 'complete'
        counts['incomplete_scan'] = int(not complete)
        summary = (f"{counts['observed_files']} fichier(s) observé(s), "
                   f"{counts['confirmed_link']} lien(s) confirmé(s), "
                   f"{counts['metadata_candidates']} correspondance(s) bibliographique(s).")
        warnings = ['Un SHA ou un ISBN ne prouve pas un droit. Aucun rattachement ni téléchargement.']
        if not complete:
            summary = 'Analyse incomplète. '+summary
            warnings.append('Certaines preuves restent non vérifiées ; un droit non vérifié ne signifie pas un livre absent.')
        control.report(message='Réconciliation locale terminée' if complete else 'Réconciliation locale incomplète')
        control.check_cancelled()
        return MaintenanceResult(ok=complete, changed=0, summary=summary,
                                 warnings=tuple(warnings), facts=counts)


RECONCILIATION_AUDIT = LibraryReconciliationAuditTool()
