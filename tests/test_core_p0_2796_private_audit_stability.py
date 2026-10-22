"""#2796: scoped private evidence comparisons, never exported book fingerprints."""
from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

from cifereca.core.library_audit_stability import (
    begin_audit_stability, finish_audit_stability,
)
from cifereca.core.library_inventory import FileObservation
from cifereca.core.library_reconciliation import EvidenceSnapshot
from cifereca.core.library_audit_broker import LibraryReconciliationReadBrokerImpl
from tests.test_core_p0_2796_reconciliation_repeatability import _fixture, _run
from tests.test_library_audit_broker import scoped


def _audit(inventory, tables):
    # The proof comparator intentionally excludes catalog bibliographic text:
    # catalogue metadata alone cannot turn a file into an attested right.
    return SimpleNamespace(
        inventory=inventory,
        snapshot=EvidenceSnapshot(tables),
        reconciliation=_run(inventory, tables),
    )


def _pair(first, second):
    owner, identity, scope = object(), 'alice', ('synthetic',)
    generation, prior = begin_audit_stability(owner, identity, scope)
    assert prior is None
    assert finish_audit_stability(owner, identity, scope, generation, prior,
                                  first, lambda: True) is None
    generation, prior = begin_audit_stability(owner, identity, scope)
    assert prior is not None
    return finish_audit_stability(owner, identity, scope, generation, prior,
                                  second, lambda: True)


def _base(count=600):
    inventory, tables = _fixture(count)
    tables['backup_items'] = []
    return inventory, tables


def test_equal_six_hundred_observations_and_sqlite_proofs_are_stable():
    inventory, tables = _base()
    first = _audit(inventory, tables)
    reordered = deepcopy(tables)
    # SQLite SELECT without ORDER BY may enumerate otherwise identical rows
    # differently. Compare table multisets, not row positions.
    for name, rows in reordered.items():
        rows.reverse()
    second = _audit(deepcopy(inventory), reordered)
    report = _pair(first, second)
    assert report['schema'] == 1
    assert report['basis'] == 'complete'
    assert report['previous_observed'] == report['current_observed'] == 600
    assert report['same_observed_cohort'] is True
    assert report['same_file_evidence'] is True
    assert report['same_sqlite_proofs'] is True
    assert report['changed_sqlite_tables'] == []
    assert report['lost_confirmed'] == report['gained_confirmed'] == 0
    assert report['interpretation'] == 'stable_inputs'
    assert 'PRIVATE' not in json.dumps(report)
    assert inventory.files[0].sha256 not in json.dumps(report)


def test_twenty_nine_sqlite_placement_path_changes_explain_a_bounded_loss():
    inventory, tables = _base()
    first = _audit(inventory, tables)
    changed = deepcopy(tables)
    for placement in changed['placements'][:29]:
        placement['path'] += '.historical'
    result = _pair(first, _audit(deepcopy(inventory), changed))
    assert result['previous_observed'] == result['current_observed'] == 600
    assert result['same_observed_cohort'] is True
    assert result['same_file_evidence'] is True
    assert result['same_sqlite_proofs'] is False
    assert result['changed_sqlite_tables'] == ['placements']
    assert result['files_added'] == result['files_removed'] == 0
    assert result['lost_confirmed'] == 29
    assert result['gained_confirmed'] == 0
    assert result['interpretation'] == 'sqlite_proofs_changed'


def test_twenty_nine_replaced_paths_keep_total_but_change_file_cohort():
    inventory, tables = _base()
    first = _audit(inventory, tables)
    replaced = deepcopy(inventory)
    for index, before in enumerate(replaced.files[:29]):
        replaced.files[index] = FileObservation(
            before.identity_id, before.path + '.replacement', before.size,
            before.media_format, before.sha256, before.validation,
            before.metadata, before.fingerprint, before.structure_validation,
        )
    result = _pair(first, _audit(replaced, deepcopy(tables)))
    assert result['previous_observed'] == result['current_observed'] == 600
    assert result['files_added'] == result['files_removed'] == 29
    assert result['same_observed_cohort'] is False
    assert result['same_sqlite_proofs'] is True
    assert result['lost_confirmed'] == 29
    assert result['gained_confirmed'] == 0
    assert result['interpretation'] == 'different_observed_cohort'


def test_unchanged_paths_but_modified_file_evidence_is_not_sqlite_drift():
    inventory, tables = _base()
    first = _audit(inventory, tables)
    altered = deepcopy(inventory)
    row = altered.files[0]
    altered.files[0] = FileObservation(
        row.identity_id, row.path, row.size, row.media_format, 'b' * 64,
        row.validation, row.metadata, row.fingerprint, row.structure_validation,
    )
    result = _pair(first, _audit(altered, deepcopy(tables)))
    assert result['same_observed_cohort'] is True
    assert result['files_changed'] == 1
    assert result['same_sqlite_proofs'] is True
    assert result['lost_confirmed'] == 1
    assert result['interpretation'] == 'file_evidence_changed'


def test_incomplete_comparison_never_explains_link_loss_by_input_delta():
    inventory, tables = _base(2)
    first = _audit(inventory, tables)
    second = _audit(deepcopy(inventory), deepcopy(tables))
    second.reconciliation.reasons.add('comparison_budget')
    result = _pair(first, second)
    assert result['basis'] == 'observed_only'
    assert result['interpretation'] == 'comparison_incomplete'


def test_scope_revocation_and_old_generations_never_publish_comparisons():
    inventory, tables = _base(1)
    audit = _audit(inventory, tables)
    owner, scope = object(), ('current-epoch',)
    g0, _ = begin_audit_stability(owner, 'alice', scope)
    g1, _ = begin_audit_stability(owner, 'alice', scope)
    assert finish_audit_stability(
        owner, 'alice', scope, g0, None, audit, lambda: True,
    ) is None
    assert finish_audit_stability(
        owner, 'alice', scope, g1, None, audit, lambda: True,
    ) is None
    g2, prior = begin_audit_stability(owner, 'alice', scope)
    assert prior is not None
    assert finish_audit_stability(
        owner, 'alice', scope, g2, prior, audit, lambda: False,
    ) is None
    _, other_identity = begin_audit_stability(owner, 'bob', scope)
    assert other_identity is None


def test_unserializable_snapshot_row_fails_closed():
    inventory, tables = _base(1)
    first = _audit(inventory, tables)
    changed = deepcopy(tables)
    changed['placements'][0]['untrusted'] = object()
    second = _audit(inventory, changed)
    assert _pair(first, second) is None


def test_broker_exposes_only_aggregate_comparison_and_rechecks_sqlite(scoped):
    service, database, _ = scoped
    broker = LibraryReconciliationReadBrokerImpl(service)
    first = broker.audit(identity_id='alice')
    assert first['status'] == 'complete'
    assert 'audit_stability' not in first
    second = broker.audit(identity_id='alice')
    assert second['status'] == 'complete'
    diff = second['audit_stability']
    assert diff['previous_observed'] == diff['current_observed'] == 1
    assert diff['same_sqlite_proofs'] is True
    assert diff['lost_confirmed'] == 0
    assert 'PRIVATE' not in json.dumps(diff)

    import sqlite3
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO entitlements (entitlement_id,identity_id,state,provider_id)"
            " VALUES (?,?,?,?)",
            ('PRIVATE_NEW_RIGHT', 'alice', 'active', 'numilog'),
        )
    third = broker.audit(identity_id='alice')
    assert third['status'] == 'complete'
    diff = third['audit_stability']
    assert diff['same_observed_cohort'] is True
    assert diff['same_sqlite_proofs'] is False
    assert diff['changed_sqlite_tables'] == ['entitlements']
    assert 'PRIVATE' not in json.dumps(third)

def test_identical_inputs_but_changed_link_result_is_flagged_not_marked_stable():
    inventory, tables = _base(3)
    original = _audit(inventory, tables)
    different = _audit(deepcopy(inventory), deepcopy(tables))
    different.reconciliation.files[0] = {'local_file_without_attested_right'}
    result = _pair(original, different)
    assert result['same_observed_cohort'] is True
    assert result['same_file_evidence'] is True
    assert result['same_sqlite_proofs'] is True
    assert result['lost_confirmed'] == 1
    assert result['interpretation'] == 'unexplained_link_drift'
    assert 'PRIVATE' not in json.dumps(result)
