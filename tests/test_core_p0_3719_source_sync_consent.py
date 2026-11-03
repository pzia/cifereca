"""One persisted source switch covers present/future v2 resources, never rights."""
from dataclasses import replace
import json
import sqlite3

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core import source_sync_consent as policy
from cifereca.core.source_acquisition_consent import (
    read_source_acquisition_consent, consent_current_in_transaction,
    set_source_acquisition_consent,
)
from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.plugin_api import SourceSyncConsentBroker
from cifereca.plugin_api.downloadable_resources import DownloadCapability, DeliveryCandidate
from tests.test_core_p0_3719_v2_execution import v2
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, register
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs


@pytest.fixture
def source(v2):
    service, db, root, calls = v2
    service._canonical_persisted_consent = lambda account: read_source_acquisition_consent(service, account)
    service._canonical_publication_consent_current = consent_current_in_transaction

    def selected(account, *, edition_id, resource_id, kind, branch, explicit_user_action):
        calls.append('offer')
        snap = service.resource_snapshot
        observed, = [r for r in snap.resources if (r.source.edition_id, r.source.resource_id) == (edition_id, resource_id)]
        return replace(snap, resources=(replace(observed, capability=DownloadCapability('available', 'offer-' + resource_id)),),
                       status='partial', coverage='partial', pagination_complete=False)

    def delivered(account, *, edition_id, resource_id, kind, branch, selected_offer_id, target_dir, explicit_user_action):
        calls.append('delivery')
        observed, = [r for r in service.resource_snapshot.resources if r.source.resource_id == resource_id]
        path = target_dir / (resource_id + '.pdf')
        path.write_bytes(BYTES)
        return DeliveryCandidate(observed.source, selected_offer_id, path)

    service.session.downloadable_selected_offer_snapshot = selected
    service.session.downloadable_selected_delivery_candidate = delivered
    return service, db, root, calls, SynchronizationBrokerImpl(service)


def change(source, enabled, revision=None):
    service, db, root, calls, broker = source
    return broker.set_source_sync_consent(broker._source_id(service.account),
                                          enabled=enabled, expected_revision=revision)


def test_explicit_on_before_inventory_covers_new_resources_without_per_right_grants(source):
    service, db, root, calls, broker = source
    sid = broker._source_id(service.account)
    assert isinstance(broker, SourceSyncConsentBroker)
    assert broker.source_sync_consent(sid) == {
        'supported': True, 'configured': False, 'enabled': False,
        'revision': None, 'valid': True,
    }
    assert refs(db) == ([], 0)
    on = change(source, True)
    assert on['enabled'] is True and len(on['revision']) == 32
    assert calls == []
    assert synchronize_account_source(service, service.account)['canonical_execution']['downloaded'] == 1
    snap = service.resource_snapshot
    added = replace(snap.resources[0], source=replace(snap.resources[0].source,
                                                     edition_id='new-edition', resource_id='new-resource', kind='loan'))
    service.resource_snapshot = replace(snap, resources=snap.resources + (added,))
    again = synchronize_account_source(service, service.account)['canonical_execution']
    assert again['present_attested'] == again['downloaded'] == 1
    assert calls == ['offer', 'delivery', 'offer', 'delivery']
    assert refs(db)[1] == 2 and len(db.placements()) == 2
    assert db.entitlements() == []
    with db._lock, db._connect() as conn:
        assert conn.execute("SELECT count(*) FROM metadata WHERE key LIKE 'sync.canonical.acquisition-consent.%'").fetchone()[0] == 0
    assert broker.canonical_acquisition_choices(sid) == ()
    off = change(source, False, on['revision'])
    assert off['revision'] != on['revision']
    assert read_source_acquisition_consent(service, service.account) == frozenset()


def test_off_overrides_existing_individual_permission_without_migrating_it(source):
    from cifereca.core.canonical_sync_stage import persist_attested_source_references
    service, db, root, calls, broker = source
    persist_attested_source_references(db, service.snapshot)
    set_source_acquisition_consent(service, service.account, service.source, allowed=True)
    assert broker.source_sync_consent(broker._source_id(service.account))['configured'] is False
    change(source, False)
    result = synchronize_account_source(service, service.account)['canonical_execution']
    assert result['downloaded'] == 0 and result['pending'] == 1
    assert calls == [] and db.placements() == []


@pytest.mark.parametrize('phase', ['offer', 'delivery', 'publication'])
def test_off_on_during_attempt_never_revives_old_generation(source, monkeypatch, phase):
    service, db, root, calls, broker = source
    on = change(source, True)
    def cycle():
        off = change(source, False, on['revision'])
        fresh = change(source, True, off['revision'])
        assert fresh['enabled'] and fresh['revision'] != on['revision']
    if phase == 'publication':
        from cifereca.core import convergence_execution
        original = convergence_execution.publish_completed_acquisition
        def publish(*args, **kwargs):
            cycle()
            return original(*args, **kwargs)
        monkeypatch.setattr(convergence_execution, 'publish_completed_acquisition', publish)
    else:
        name = 'downloadable_selected_offer_snapshot' if phase == 'offer' else 'downloadable_selected_delivery_candidate'
        original = getattr(service.session, name)
        def wrapped(*args, **kwargs):
            result = original(*args, **kwargs)
            cycle()
            return result
        setattr(service.session, name, wrapped)
    result = synchronize_account_source(service, service.account)['canonical_execution']
    assert result['downloaded'] == 0
    assert calls.count('delivery') == (0 if phase == 'offer' else 1)
    assert db.placements() == [] and refs(db)[1] == 0


def test_stale_writer_cannot_replace_newer_source_consent(source):
    on = change(source, True)
    off = change(source, False, on['revision'])
    with pytest.raises(ValueError, match='source_sync_consent_changed'):
        change(source, True, on['revision'])
    assert source[-1].source_sync_consent(source[-1]._source_id(source[0].account)) == off


@pytest.mark.parametrize('field', ['identity_id', 'provider_id', 'credential_id', 'account_id'])
def test_policy_key_is_exact_for_every_source_coordinate(source, field):
    service, db, root, calls, broker = source
    change(source, True)
    scope = policy.scope_for_account(service.account)
    foreign = replace(scope, **{field: 'different'})
    assert policy.policy_key(scope) != policy.policy_key(foreign)
    assert policy.read_policy(db, foreign).mode == 'unset'


def test_corrupt_policy_denies_and_requires_explicit_cas_repair(source):
    service, db, root, calls, broker = source
    key = policy.policy_key(policy.scope_for_account(service.account))
    db.metadata_set(key, '{bad-json')
    state = broker.source_sync_consent(broker._source_id(service.account))
    assert state['configured'] is True and state['valid'] is False and state['enabled'] is False
    result = synchronize_account_source(service, service.account)['canonical_execution']
    assert result['downloaded'] == 0 and calls == []
    with pytest.raises(ValueError, match='source_sync_consent_changed'):
        change(source, True)
    repaired = change(source, False, state['revision'])
    assert repaired['valid'] and not repaired['enabled']


def test_sql_failure_rolls_back_boolean_and_generation_together(source):
    service, db, root, calls, broker = source
    on = change(source, True)
    key = policy.policy_key(policy.scope_for_account(service.account))
    with db._lock, db._connect() as conn:
        conn.execute("CREATE TRIGGER refuse_policy BEFORE UPDATE ON metadata WHEN OLD.key='" + key + "' BEGIN SELECT RAISE(ABORT,'synthetic-write-failure'); END")
    try:
        with pytest.raises(sqlite3.IntegrityError, match='synthetic-write-failure'):
            change(source, False, on['revision'])
        assert broker.source_sync_consent(broker._source_id(service.account)) == on
    finally:
        with db._lock, db._connect() as conn:
            conn.execute('DROP TRIGGER refuse_policy')


def test_disabled_source_cannot_opt_in_but_can_revoke_without_provider_contract(source):
    service, db, root, calls, broker = source
    on = change(source, True)
    service.enabled = False
    del service.session.downloadable_selected_delivery_candidate
    with pytest.raises(ValueError, match='source_sync_consent_not_supported'):
        change(source, True, on['revision'])
    off = change(source, False, on['revision'])
    assert off['configured'] and not off['enabled'] and not off['supported']
    assert calls == []


def test_reopening_database_preserves_source_consent_and_off(source):
    from cifereca.storage import Storage
    service, db, root, calls, broker = source
    on = change(source, True)
    other = Storage(db.path)
    try:
        scope = policy.scope_for_account(service.account)
        assert policy.read_policy(other, scope).revision == on['revision']
        change(source, False, on['revision'])
        assert policy.read_policy(other, scope).mode == 'off'
    finally:
        other.close()


def test_inventory_off_on_cannot_revive_acquisition_and_advances_generation(source, monkeypatch):
    service, db, root, calls, broker = source
    on = change(source, True)
    sid = broker._source_id(service.account)
    monkeypatch.setattr(SynchronizationBrokerImpl, '_ref', lambda self, account: None)
    original = service.session.downloadable_selected_delivery_candidate
    def delivered(*args, **kwargs):
        result = original(*args, **kwargs)
        broker.set_enabled(sid, False)
        broker.set_enabled(sid, True)
        return result
    service.session.downloadable_selected_delivery_candidate = delivered
    result = synchronize_account_source(service, service.account)['canonical_execution']
    assert result['downloaded'] == 0 and db.placements() == []
    state = broker.source_sync_consent(sid)
    assert state['revision'] != on['revision'] and not state['enabled']
    assert db.metadata_get(broker._preference_key(sid)) == '1'
    assert read_source_acquisition_consent(service, service.account) == frozenset()


def test_two_database_writers_cannot_both_create_the_first_grant(source):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from copy import copy
    from cifereca.storage import Storage
    service, db, root, calls, broker = source
    other = Storage(db.path)
    clone = copy(service)
    clone.storage = other
    barrier = Barrier(2)
    def writer(target):
        barrier.wait(timeout=5)
        try:
            policy.set_source_sync_consent(target, target.account, enabled=True, expected_revision=None)
            return 'committed'
        except ValueError as exc:
            assert str(exc) == 'source_sync_consent_changed'
            return 'stale'
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(writer, (service, clone)))
        assert sorted(results) == ['committed', 'stale']
        assert calls == []
    finally:
        other.close()


def test_source_toggle_and_policy_rollback_as_one_transaction(source, monkeypatch):
    service, db, root, calls, broker = source
    on = change(source, True)
    sid = broker._source_id(service.account)
    key = policy.policy_key(policy.scope_for_account(service.account))
    with db._lock, db._connect() as conn:
        conn.execute("CREATE TRIGGER refuse_policy BEFORE UPDATE ON metadata WHEN OLD.key='" + key + "' BEGIN SELECT RAISE(ABORT,'synthetic-write-failure'); END")
    try:
        with pytest.raises(sqlite3.IntegrityError, match='synthetic-write-failure'):
            broker.set_enabled(sid, False)
        assert db.metadata_get(broker._preference_key(sid)) is None
        assert broker.source_sync_consent(sid) == on
    finally:
        with db._lock, db._connect() as conn:
            conn.execute('DROP TRIGGER refuse_policy')


def test_source_disabled_inside_sql_snapshot_cannot_grant_even_if_cached_read_says_on(source):
    service, db, root, calls, broker = source
    sid = broker._source_id(service.account)
    db.metadata_set(broker._preference_key(sid), '0')
    assert service.sync_source_enabled(service.account) is True  # intentionally stale test facade
    with pytest.raises(ValueError, match='source_sync_consent_source_disabled'):
        change(source, True)
    assert not broker.source_sync_consent(sid)['configured']
    assert calls == []


def test_final_sql_guard_rechecks_source_enablement_not_just_consent_epoch(source, monkeypatch):
    from cifereca.core import convergence_execution
    service, db, root, calls, broker = source
    change(source, True)
    sid = broker._source_id(service.account)
    original = convergence_execution.publish_completed_acquisition
    def publish(*args, **kwargs):
        # Represents a separate source-disable writer winning immediately
        # before publication's BEGIN IMMEDIATE. The policy row itself is ON.
        db.metadata_set(broker._preference_key(sid), '0')
        return original(*args, **kwargs)
    monkeypatch.setattr(convergence_execution, 'publish_completed_acquisition', publish)
    result = synchronize_account_source(service, service.account)['canonical_execution']
    assert result['downloaded'] == 0
    assert db.placements() == [] and refs(db)[1] == 0
    assert calls == ['offer', 'delivery']

@pytest.mark.parametrize('field', ['enabled', 'schema', 'scope', 'revision'])
def test_duplicate_policy_fields_deny_delivery_and_require_cas_repair(source, field):
    service, db, root, calls, broker = source
    change(source, True)
    scope = policy.scope_for_account(service.account)
    key = policy.policy_key(scope)
    valid = db.metadata_get(key)
    value = {'enabled': False, 'schema': 'old-schema',
             'scope': ['foreign'] * 4, 'revision': 'b' * 32}[field]
    raw = '{' + json.dumps(field) + ':' + json.dumps(value) + ',' + valid[1:]
    assert json.loads(raw)['enabled'] is True
    db.metadata_set(key, raw)
    sid = broker._source_id(service.account)
    state = broker.source_sync_consent(sid)
    assert state['configured'] and not state['valid'] and not state['enabled']
    assert state['revision'].startswith('invalid:')
    result = synchronize_account_source(service, service.account)['canonical_execution']
    assert result['downloaded'] == 0 and calls == [] and db.placements() == []
    assert db.metadata_get(key) == raw  # no repair or migration at read time
    with pytest.raises(ValueError, match='source_sync_consent_changed'):
        change(source, True)
    repaired = change(source, False, state['revision'])
    assert repaired['valid'] and not repaired['enabled']
    assert repaired['revision'] != state['revision']




@pytest.mark.parametrize("resource_count", [0, 2, 4])
def test_source_off_reports_exact_number_of_withheld_resources(
    source, resource_count,
):
    service, db, _root, calls, _broker = source
    original = service.resource_snapshot
    sample = original.resources[0]
    resources = tuple(
        replace(sample, source=replace(
            sample.source,
            edition_id="off-edition-" + str(index),
            resource_id="off-resource-" + str(index),
        ))
        for index in range(resource_count)
    )
    service.resource_snapshot = replace(original, resources=resources)
    off = change(source, False)
    assert off["configured"] and not off["enabled"]
    result = synchronize_account_source(service, service.account)
    execution = result["canonical_execution"]
    assert execution["pending"] == resource_count
    assert execution["downloaded"] == 0
    assert execution["recovered"] == 0
    assert execution["blocked"] == 0
    assert calls == []  # Not even a selected-offer preflight.
    assert db.placements() == []



@pytest.mark.parametrize('invalid', ['false', 'true', 0, 1, None, [], {}])
def test_internal_source_toggle_rejects_nonboolean_without_any_mutation(source, invalid):
    service, db, _root, calls, broker = source
    on = change(source, True)
    sid = broker._source_id(service.account)
    preference_key = broker._preference_key(sid)
    assert db.metadata_get(preference_key) is None
    with pytest.raises(ValueError, match='invalid_source_sync_toggle'):
        policy.set_source_enabled(service, service.account, invalid, preference_key)
    assert db.metadata_get(preference_key) is None
    assert broker.source_sync_consent(sid) == on
    assert calls == []


def test_internal_source_toggle_cannot_update_foreign_or_policy_metadata(source):
    service, db, _root, calls, broker = source
    on = change(source, True)
    sid = broker._source_id(service.account)
    scope = policy.scope_for_account(service.account)
    foreign = 'sync.source.enabled.sync:foreign-source-not-the-bound-account'
    local_pref = broker._preference_key(sid)
    policy_row = policy.policy_key(scope)
    before_policy = db.metadata_get(policy_row)
    assert before_policy is not None
    for wrong_key in (foreign, policy_row, '', None):
        with pytest.raises(ValueError, match=(
            'invalid_source_sync_toggle' if wrong_key is None
            else 'source_sync_toggle_scope_mismatch'
        )):
            policy.set_source_enabled(service, service.account, False, wrong_key)
    assert db.metadata_get(foreign) is None
    assert db.metadata_get(local_pref) is None
    assert db.metadata_get(policy_row) == before_policy
    assert broker.source_sync_consent(sid) == on
    assert calls == []


@pytest.mark.parametrize("variant", ("guarded", "runtime_guarded"))
def test_source_consent_accepts_guarded_only_v2_delivery_without_legacy_get(
    source, variant,
):
    """A provider need not expose an unguarded content endpoint for Core Sync."""
    service, db, root, calls, broker = source
    del service.session.downloadable_selected_delivery_candidate
    service.session.rights_selected_delivery_candidate = (
        lambda *_args, **_kwargs: pytest.fail("unguarded legacy GET called")
    )
    captured_admission = []

    def execute_runtime(request):
        assert request["schema_version"] == 2
        assert request["source"]["type"] == "kobo"
        assert request["format"] == "pdf"
        destination = request["destination"]
        from pathlib import Path
        Path(destination).write_bytes(BYTES)
        return {"ok": True, "artifact": {"path": destination}}

    service.execute_acquisition_request = execute_runtime

    def selected_candidate(account, *, edition_id, resource_id, kind,
                           branch, selected_offer_id, target_dir,
                           explicit_user_action, admission_current,
                           **extra):
        assert explicit_user_action is True
        assert account is service.account
        assert (edition_id, resource_id, kind, branch) == (
            service.source.edition_id, service.source.right_id,
            service.source.kind, service.source.branch,
        )
        assert selected_offer_id == "offer-" + resource_id
        assert admission_current() is True
        calls.append("guarded")
        path = target_dir / "received.pdf"
        if variant == "runtime_guarded":
            assert set(extra) == {"execute_runtime"}
            extra["execute_runtime"]({
                "schema_version": 2,
                "source": {"type": "kobo", "provider": "synthetic"},
                "destination": str(path),
                "format": "pdf",
            })
        else:
            assert extra == {}
            path.write_bytes(BYTES)
        captured_admission.append(admission_current)
        return DeliveryCandidate(
            service.resource_snapshot.resources[0].source,
            selected_offer_id, path,
        )

    method = ("downloadable_selected_delivery_candidate_runtime_guarded"
              if variant == "runtime_guarded"
              else "downloadable_selected_delivery_candidate_guarded")
    setattr(service.session, method, selected_candidate)

    sid = broker._source_id(service.account)
    assert broker.source_sync_consent(sid)["supported"] is True
    assert change(source, True)["enabled"] is True
    first = synchronize_account_source(service, service.account)["canonical_execution"]
    assert first["downloaded"] == 1
    assert first["failed"] == first["pending"] == 0
    assert calls == ["offer", "guarded"]
    assert refs(db)[1] == 1 and len(db.placements()) == 1
    assert all(not lease() for lease in captured_admission)

    second = synchronize_account_source(service, service.account)["canonical_execution"]
    assert second["present_attested"] == 1 and second["downloaded"] == 0
    assert calls == ["offer", "guarded"]
    assert refs(db)[1] == 1 and len(db.placements()) == 1
