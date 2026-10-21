"""Public broker consumer: planning, cancellation and numeric-only rendering."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_reconciliation import LibraryReconciliationAuditTool


def report():
    return {'schema': 1, 'read_only': True, 'acquisition_authorized': False,
            'repair_authorized': False, 'status': 'complete',
            'inventory': {'files': 524, 'hashed_files': 517},
            'reconciliation': {'counts': {'confirmed_link': 253, 'present_unlinked': 264,
                                         'rights_without_verified_file': 5}},
            'metadata_candidates': {'metadata_candidates': 7},
            'stored_paths': {'counts': {'stored_path_missing': 1}},
            'private_title': 'PRIVATE', 'reasons': [], 'warnings': ['PRIVATE']}


class Control:
    def __init__(self):
        self.stop = False
        self.events = []
    def check_cancelled(self):
        if self.stop:
            raise RuntimeError('cancelled')
    def cancelled(self): return self.stop
    def report(self, **kw): self.events.append(kw)


class Broker:
    def __init__(self):
        self.calls = []
        self.result = report()
    def audit(self, **kw):
        self.calls.append(kw['identity_id'])
        kw['control'].report(current=1, total=None, message='Inventaire local')
        return self.result


@pytest.fixture
def tool():
    broker = Broker()
    instance = LibraryReconciliationAuditTool().activate(SimpleNamespace(broker=lambda name: broker))
    return instance, broker


def prepare(instance):
    return instance.plan(MaintenanceContext(scope='library', identity_id='PRIVATE_IDENTITY'))


def test_planning_never_audits_and_never_exports_identity(tool):
    instance, broker = tool
    plan = prepare(instance)
    assert not broker.calls
    assert 'PRIVATE' not in repr(plan)
    assert instance.describe().danger_level == 'read-only'
    assert instance.describe().cancellable


def test_run_calls_broker_with_bound_identity_and_renders_only_known_counts(tool):
    instance, broker = tool
    control = Control()
    result = instance.run(prepare(instance), control)
    assert broker.calls == ['PRIVATE_IDENTITY']
    assert result.ok and result.changed == 0
    assert result.facts['observed_files'] == 524
    assert result.facts['hashed_files'] == 517
    assert result.facts['present_unlinked'] == 264
    assert result.facts['rights_without_verified_file'] == 5
    assert '253' in result.summary
    assert 'PRIVATE' not in repr(result)
    assert control.events[0]['current'] == 1
    assert control.events[-1]['message'] == 'Réconciliation locale terminée'


def test_incomplete_scan_retains_uncertainty_and_never_calls_rights_missing(tool):
    instance, broker = tool
    broker.result['status'] = 'incomplete_scan'
    result = instance.run(prepare(instance), Control())
    assert not result.ok and result.changed == 0
    assert result.facts['incomplete_scan'] == 1
    assert 'ne signifie pas un livre absent' in ' '.join(result.warnings)
    assert 'PRIVATE' not in repr(result)


def test_closed_limit_causes_are_explained_without_rendering_unknown_prose(tool):
    instance, broker = tool
    broker.result['status'] = 'incomplete_scan'
    broker.result['reasons'] = ['hash_byte_budget', 'snapshot_time_budget', 'PRIVATE', 'hash_byte_budget']
    result = instance.run(prepare(instance), Control())
    assert not result.ok
    assert result.warnings.count('Budget de lecture SHA atteint.') == 1
    assert 'Durée maximale de lecture de la base atteinte.' in result.warnings
    assert 'PRIVATE' not in repr(result)


def test_unavailable_migration_has_a_closed_explanation_and_no_zero_files(tool):
    instance, broker = tool
    broker.result = {'schema': 1, 'read_only': True, 'repair_authorized': False,
                     'acquisition_authorized': False, 'status': 'incomplete_scan',
                     'reasons': ['snapshot_identity_migration']}
    result = instance.run(prepare(instance), Control())
    assert not result.ok
    assert 'Une migration de bibliothèque doit être terminée avant l’analyse.' in result.warnings
    assert 'observed_files' not in result.facts


@pytest.mark.parametrize('reasons', [['hash_byte_budget'], {}, True])
def test_inconsistent_complete_with_limits_is_never_success(tool, reasons):
    instance, broker = tool
    broker.result['reasons'] = reasons
    assert not instance.run(prepare(instance), Control()).ok


@pytest.mark.parametrize('change', ['copy', 'cross-tool', 'mutated-facts', 'stale', 'deactivated'])
def test_unbound_stale_or_modified_plans_never_call_core(tool, change):
    instance, broker = tool
    plan = prepare(instance)
    if change == 'copy': plan = deepcopy(plan)
    elif change == 'cross-tool': plan = replace(plan, tool_id='other')
    elif change == 'mutated-facts': plan.facts['available'] = False
    elif change == 'stale': prepare(instance)
    else: instance.deactivate()
    result = instance.run(plan, Control())
    assert not result.ok and result.changed == 0
    assert not broker.calls


def test_plan_is_single_use(tool):
    instance, broker = tool
    plan = prepare(instance)
    assert instance.run(plan, Control()).ok
    assert not instance.run(plan, Control()).ok
    assert len(broker.calls) == 1


def test_old_core_missing_broker_stays_unavailable_without_fallback():
    instance = LibraryReconciliationAuditTool().activate(SimpleNamespace(broker=lambda name: None))
    plan = prepare(instance)
    assert plan.facts['available'] is False
    assert not instance.run(plan, Control()).ok


@pytest.mark.parametrize('field,value', [
    ('status', {}), ('schema', True), ('read_only', False),
    ('repair_authorized', True), ('acquisition_authorized', True),
    ('inventory', {'files': 'PRIVATE', 'hashed_files': 1}),
    ('metadata_candidates', {'metadata_candidates': -1}),
    ('reconciliation', {'counts': {'confirmed_link': True}}),
    ('stored_paths', {'counts': {'stored_path_missing': 1_000_001}}),
])
def test_malformed_core_payloads_never_render_private_prose_or_invent_success(tool, field, value):
    instance, broker = tool
    broker.result[field] = value
    result = instance.run(prepare(instance), Control())
    assert not result.ok and not result.changed
    assert 'PRIVATE' not in repr(result)


def test_closed_unavailable_core_response_does_not_become_zero_files(tool):
    instance, broker = tool
    broker.result = {'schema': 1, 'status': 'incomplete_scan', 'read_only': True,
                     'repair_authorized': False, 'acquisition_authorized': False,
                     'reasons': ['PRIVATE']}
    result = instance.run(prepare(instance), Control())
    assert not result.ok and 'observed_files' not in result.facts
    assert 'PRIVATE' not in repr(result)


def test_core_exception_is_closed_and_cancellation_is_not_swallowed(tool):
    instance, broker = tool
    def fail(**kw): raise RuntimeError('PRIVATE')
    broker.audit = fail
    result = instance.run(prepare(instance), Control())
    assert not result.ok and 'PRIVATE' not in repr(result)
    control = Control()
    control.stop = True
    with pytest.raises(RuntimeError, match='cancelled'):
        instance.run(prepare(instance), control)


def test_cancellation_after_last_core_item_remains_cancelled(tool):
    instance, broker = tool
    def cancelled_result(**kw):
        kw['control'].stop = True
        return report()
    broker.audit = cancelled_result
    with pytest.raises(RuntimeError, match='cancelled'):
        instance.run(prepare(instance), Control())


def test_all_counter_labels_are_explicit_safe_and_preserve_uncertainty(tool, monkeypatch):
    import cifereca_library_reconciliation as module
    captured = []
    class PresentableResult:
        details = ()
        def __init__(self, **kwargs):
            captured.append(kwargs)
    monkeypatch.setattr(module, 'MaintenanceResult', PresentableResult)
    instance, broker = tool
    broker.result['reconciliation']['counts']['untrusted_private'] = 'PRIVATE'
    instance.run(prepare(instance), Control())
    details = captured[0]['details']
    assert 'Fichiers observés : 524' in details
    assert 'Fichiers avec SHA vérifié : 517' in details
    assert 'Liens confirmés par les preuves locales : 253' in details
    assert 'Droits sans fichier local vérifié : 5' in details
    assert 'Chemins enregistrés absents au contrôle : 1' in details
    assert 'Candidats bibliographiques sans preuve de droit : 7' in details
    assert 'ne pas additionner' in details[-1]
    assert len(details) == 14
    assert 'PRIVATE' not in repr(captured)


def test_old_core_without_presentation_capability_keeps_existing_result(tool, monkeypatch):
    import cifereca_library_reconciliation as module
    from dataclasses import dataclass, field
    @dataclass
    class OldResult:
        ok: bool
        changed: int
        summary: str
        warnings: tuple
        facts: dict = field(default_factory=dict)
    monkeypatch.setattr(module, 'MaintenanceResult', OldResult)
    instance, broker = tool
    result = instance.run(prepare(instance), Control())
    assert result.ok and result.changed == 0
    assert result.facts['hashed_files'] == 517
    assert '253' in result.summary
    assert not hasattr(result, 'details')
