"""ABI v2-only providers use the existing locked Sync and Core publication."""
from dataclasses import replace

import pytest

from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.plugin_api.downloadable_resources import (
    DeliveryCandidate, DownloadCapability, OptionalRightMetadata,
    from_legacy_rights_snapshot,
)
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs


@pytest.fixture
def v2(rig, monkeypatch):
    service, db, root = rig
    monkeypatch.setattr(
        'cifereca.core.canonical_selected_offers.acquisition_lock_file',
        lambda: root.parent / 'state' / 'acquisition.lock',
    )
    monkeypatch.setattr(
        'cifereca.core.canonical_selected_delivery.acquisition_lock_file',
        lambda: root.parent / 'state' / 'acquisition.lock',
    )
    snapshot = from_legacy_rights_snapshot(service.snapshot)
    snapshot = replace(snapshot, resources=(replace(snapshot.resources[0], right=None),))
    service.resource_snapshot = snapshot
    calls = []
    del service.session.rights_observation_snapshot
    service.session.downloadable_resource_snapshot = lambda account: service.resource_snapshot
    # The existing source-wide decision callback admits every resource of this
    # synthetic source. No individual-consent operation is performed here.
    service._canonical_persisted_consent = lambda account: frozenset({service.source})

    def selected(account, *, edition_id, resource_id, kind, branch, explicit_user_action):
        assert explicit_user_action is True
        assert (edition_id, resource_id, kind, branch) == (
            service.source.edition_id, service.source.right_id,
            service.source.kind, service.source.branch,
        )
        calls.append('offer')
        current = service.resource_snapshot
        return replace(current, resources=(replace(
            current.resources[0], capability=DownloadCapability('available', 'v2-offer'),
        ),), status='partial', coverage='partial', pagination_complete=False)

    def candidate(account, *, edition_id, resource_id, kind, branch,
                  selected_offer_id, target_dir, explicit_user_action):
        assert explicit_user_action is True and selected_offer_id == 'v2-offer'
        calls.append('delivery')
        path = target_dir / 'received.pdf'
        path.write_bytes(BYTES)
        return DeliveryCandidate(snapshot.resources[0].source, selected_offer_id, path)

    service.session.downloadable_selected_offer_snapshot = selected
    service.session.downloadable_selected_delivery_candidate = candidate
    return service, db, root, calls


@pytest.mark.parametrize('right_state', [None, 'unknown', 'inactive'])
@pytest.mark.parametrize('legacy_present', [False, True])
def test_v2_only_download_publishes_once_without_inventing_right(v2, right_state, legacy_present):
    service, db, root, calls = v2
    snapshot = service.resource_snapshot
    service.resource_snapshot = replace(snapshot, resources=(replace(
        snapshot.resources[0], right=(None if right_state is None
                                    else OptionalRightMetadata(right_state)),
    ),))
    if legacy_present:
        for name in ('rights_observation_snapshot', 'rights_selected_delivery_offer_snapshot',
                     'rights_selected_delivery_candidate'):
            setattr(service.session, name, lambda *_a, **_kw: pytest.fail('no second v1 call'))
    first = synchronize_account_source(service, service.account)
    assert first['canonical_execution']['downloaded'] == 1
    assert calls == ['offer', 'delivery']
    assert refs(db)[1] == 1 and len(db.placements()) == 1
    assert db.entitlements() == []
    assert next((root / 'A').rglob('received.pdf')).read_bytes() == BYTES
    second = synchronize_account_source(service, service.account)
    assert second['canonical_execution']['present_attested'] == 1
    assert second['canonical_execution']['downloaded'] == 0
    assert calls == ['offer', 'delivery']
    assert refs(db)[1] == 1 and len(db.placements()) == 1


@pytest.mark.parametrize('field', ['source', 'scope', 'branch', 'offer', 'untyped', 'outside'])
def test_v2_candidate_must_match_exact_source_offer_and_core_staging(v2, field):
    service, db, root, calls = v2
    original = service.session.downloadable_selected_delivery_candidate
    def invalid(*args, **kwargs):
        result = original(*args, **kwargs)
        if field == 'source':
            return replace(result, source=replace(result.source, resource_id='other-resource'))
        if field == 'scope':
            return replace(result, source=replace(result.source, scope=replace(
                result.source.scope, identity_id='other-profile',
            )))
        if field == 'branch':
            return replace(result, source=replace(result.source, branch='other-branch'))
        if field == 'offer':
            return replace(result, selected_offer_id='other-offer')
        if field == 'untyped':
            return result.path, result.provider_hint
        path = root / 'A' / 'outside.pdf'
        path.write_bytes(BYTES)
        return replace(result, path=path)
    service.session.downloadable_selected_delivery_candidate = invalid
    service.session.rights_selected_delivery_candidate = lambda *_a, **_kw: pytest.fail('no fallback')
    result = synchronize_account_source(service, service.account)
    assert result['canonical_execution']['failed'] == 1
    assert db.placements() == [] and refs(db)[1] == 0
    assert calls == ['offer', 'delivery']


@pytest.mark.parametrize('phase', ['inventory', 'offer'])
@pytest.mark.parametrize('bad_result', ['exception', 'legacy'])
def test_failed_v2_reader_never_retries_v1(v2, phase, bad_result):
    service, db, root, calls = v2
    def fail(*_a, **_kw):
        calls.append(phase)
        if bad_result == 'exception':
            raise ValueError('synthetic-v2-failure')
        return service.snapshot
    if phase == 'inventory':
        service.session.downloadable_resource_snapshot = fail
        legacy = 'rights_observation_snapshot'
    else:
        service.session.downloadable_selected_offer_snapshot = fail
        legacy = 'rights_selected_delivery_offer_snapshot'
    setattr(service.session, legacy, lambda *_a, **_kw: pytest.fail('no fallback'))
    result = synchronize_account_source(service, service.account)
    assert 'delivery' not in calls
    assert db.placements() == [] and refs(db)[1] == 0
    assert result.get('canonical_execution', {}).get('downloaded', 0) == 0


def test_source_off_never_selects_or_gets_v2_resource(v2):
    service, db, root, calls = v2
    service._canonical_persisted_consent = lambda account: frozenset()
    result = synchronize_account_source(service, service.account)
    assert result['canonical_execution']['downloaded'] == 0
    assert calls == [] and db.placements() == [] and refs(db)[1] == 0


def test_rebound_during_v2_offer_never_gets_candidate(v2):
    service, db, root, calls = v2
    original = service.session.downloadable_selected_offer_snapshot
    def rebound(*args, **kwargs):
        result = original(*args, **kwargs)
        service.session.accounts = []
        return result
    service.session.downloadable_selected_offer_snapshot = rebound
    with pytest.raises(ValueError, match='canonical_offer_source_rebound'):
        synchronize_account_source(service, service.account)
    assert calls == ['offer'] and db.placements() == [] and refs(db)[1] == 0


def test_installed_reference_provider_v2_receives_epub_then_restarts_without_get(v2, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from cifereca.core.source_convergence import SourceKey
    from tests.test_core_p0_3171_atomic_publication_v14 import register

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] /
                                   'src' / 'plugins' / 'provider-reference'))
    from cifereca_reference import ReferenceSession
    service, db, root, _calls = v2
    session = ReferenceSession(
        credential_id='reference-card', label='Offline', credentials={},
        options={'accounts': 'one'},
    )
    account = session.connect()[0]
    account.identity_id = 'A'
    session.identity_broker = SimpleNamespace(accounts=lambda identity_id=None: (
        SimpleNamespace(scope_version=2, provider_id='reference',
                        credential_id='reference-card', account_id=account.unique_id,
                        identity_id='A'),
    ))
    # Exercise the installed provider's real v2 adapter on the fixture with
    # absent right metadata. Its v1 translation is internal to the provider.
    session._resources = tuple(row for row in session._resources
                               if row.edition_id == 'epub-002')
    register(db, 'A', 'reference', 'reference-card', account=account.unique_id)
    service.account = account
    service.accounts = [account]
    service.session = session
    service._sessions = {service._session_key('reference', 'reference-card'): session}
    snapshot = session.downloadable_resource_snapshot(account)
    assert len(snapshot.resources) == 1 and snapshot.resources[0].right is None
    ref = snapshot.resources[0].source
    service.source = SourceKey(ref.scope, ref.edition_id, ref.resource_id,
                               ref.kind, ref.branch)
    try:
        first = synchronize_account_source(service, account)
        assert first['canonical_execution']['downloaded'] == 1
        assert session._delivery_calls == 1
        assert refs(db)[1] == 1 and db.entitlements() == []
        placements = db.placements()
        assert len(placements) == 1 and placements[0]['identity_id'] == 'A'
        assert next((root / 'A').rglob('*.epub')).is_file()
        # A new Core epoch models re-entry after session context refresh;
        # committed OriginLink/SHA/Placement remains the sole local proof.
        service._source_context_epoch += 1
        second = synchronize_account_source(service, account)
        assert second['canonical_execution']['present_attested'] == 1
        assert second['canonical_execution']['downloaded'] == 0
        assert session._delivery_calls == 1
        assert db.placements() == placements and refs(db)[1] == 1
    finally:
        session.close()
