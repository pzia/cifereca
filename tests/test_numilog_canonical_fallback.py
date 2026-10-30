"""Deterministic receipt/EPUB/SQLite acceptance, no live Numilog/ADEPT call."""
from dataclasses import replace
import hashlib
from io import BytesIO
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core import numilog_materialization_startup as startup
from cifereca.core.numilog_manifest_rights import mirror_numilog_manifest_rights
from cifereca.plugin_api import ProviderRightsScope, ProviderRightsSnapshot, ProviderRightObservation
from tests.test_core_p0_1862_numilog_store_mirror import _connect, _row
from tests.test_core_p0_numilog_auto_materialization import _authorize
from tests.test_library_reconciliation import epub as _write_epub
from tests.test_sync_state_contract import _service

STAMP = '2026-10-30T05:17:41+00:00'


def install(service, tmp_path, *, identity='default', credential='numilog-private', count=6):
    account, session, _ = _connect(service, identity=identity, credential=credential, number=0)
    session.accounts = [account]
    connection = next(c for c in service.storage.provider_connections(identity_id=identity)
                      if c['credential_id'] == credential)
    service.storage.provider_account_put(dict(
        connection_id=connection['connection_id'], identity_id=identity,
        provider_id='numilog', credential_id=credential, account_id=account.unique_id))
    rows = [_row(credential, i) for i in range(count)]
    session.library = lambda: list(rows)
    scope = ProviderRightsScope(identity, 'numilog', credential, account.unique_id)
    rights = tuple(ProviderRightObservation(
        row.id, row.id, 'purchase', 'active', 'provider_response',
        'synthetic-inventory-' + str(i), STAMP) for i, row in enumerate(rows))
    state = NS(snapshot=ProviderRightsSnapshot(
        scope, rights, status='partial', coverage='partial', branch='meslivres', observed_at=STAMP),
        downloads=[], offers=[], rows=rows, account=account, session=session, service=service)
    template = tmp_path / 'template.epub'
    _write_epub(template)
    state.contents = []
    for i in range(count):
        content = BytesIO(template.read_bytes())
        with zipfile.ZipFile(content, 'a') as archive:
            archive.writestr('book-number.txt', str(i))
        state.contents.append(content.getvalue())
    session.rights_observation_snapshot = lambda _: state.snapshot

    def offer(_account, *, right_id, **kwargs):
        state.offers.append(right_id)
        original = next(r for r in state.snapshot.rights if r.right_id == right_id)
        return replace(state.snapshot, rights=(replace(original, delivery='available',
                       delivery_offer_id='signed-offer-' + right_id),))

    def candidate(_account, *, right_id, target_dir, **kwargs):
        state.downloads.append(right_id)
        index = next(i for i, row in enumerate(rows) if row.id == right_id)
        path = Path(target_dir) / 'received.epub'
        path.write_bytes(state.contents[index])
        return path, 'https://synthetic.invalid/delivery'

    session.rights_selected_delivery_offer_snapshot = offer
    session.rights_selected_delivery_candidate = candidate
    state.candidate = candidate
    state.broker = SynchronizationBrokerImpl(service)
    state.sid = state.broker._source_id(account)
    assert mirror_numilog_manifest_rights(service, rows, identity_id=identity,
           credential_id=credential, account_id=account.unique_id) == count
    return state


@pytest.fixture
def rig(tmp_path):
    service = _service(tmp_path)
    state = install(service, tmp_path)
    yield state
    service.close()


def authorize(state):
    _authorize(state.broker, state.sid)


def seed(state, index, *, nested=False, attested=True):
    service, account = state.service, state.account
    item = state.rows[index].id
    directory = service.identity_by_id(account.identity_id).directory_name
    path = service.library_dir() / directory / 'numilog'
    if nested:
        path /= 'acsm-historical-%s' % index
    path /= 'book-%s.epub' % index
    path.parent.mkdir(parents=True, exist_ok=True)
    data = state.contents[index]
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    artifact = 'sha256:' + digest
    service.storage.artifact_put(dict(artifact_id=artifact, sha256=digest,
        size=len(data), media_format='epub', kind='file'))
    service.storage.placement_put(dict(path=str(path), artifact_id=artifact,
        identity_id=account.identity_id, provider_id='numilog',
        source_item_id=item if attested else 'historical-acsm-%s' % index, state='present'))
    if attested:
        right = next(r for r in service.storage.entitlements() if r['item_id'] == item)
        service.storage.acquisition_put(dict(acquisition_id='historical-%s-%s' % (account.identity_id, index),
            entitlement_id=right['entitlement_id'], provider_id='numilog',
            credential_id=account.credential_id, identity_id=account.identity_id,
            account_id=account.unique_id, item_id=item, artifact_id=artifact, status='complete'))
    else:
        service.storage.acquisition_put(dict(acquisition_id='unresolved-%s-%s' % (account.identity_id, index),
            provider_id='numilog', credential_id=account.credential_id,
            identity_id=account.identity_id, account_id=account.unique_id, item_id=item,
            status='attention_required', attention_code='numilog_index_pending'))
    return path


def sql(service, query):
    with service.storage._lock, service.storage._connect() as db:
        return db.execute(query).fetchall()


@pytest.mark.parametrize('nested', [False, True])
def test_six_exact_receipts_any_subdirectory_no_get_on_two_syncs(rig, nested):
    paths = [seed(rig, i, nested=nested and i >= 3) for i in range(6)]
    before = [p.read_bytes() for p in paths]
    authorize(rig)
    for _ in range(2):
        result = rig.service.synchronize_account_source(rig.account)
        assert result['canonical_execution']['present_attested'] == 6
        assert result['canonical_execution']['downloaded'] == 0
    assert not rig.downloads and not rig.offers
    assert sql(rig.service, 'SELECT count(*) FROM origin_links') == [(6,)]
    assert len(rig.service.storage.placements()) == 6
    assert [p.read_bytes() for p in paths] == before


def test_three_unresolved_receipts_fallback_reuses_sha_preserves_all_placements(rig):
    paths = [seed(rig, i, nested=i >= 3, attested=i < 3) for i in range(6)]
    before = [p.read_bytes() for p in paths]
    authorize(rig)
    # The passive historical preflight remains a diagnostic, not a veto.
    assert all(rig.broker.preflight_numilog_purchase(rig.sid, r.id).facts['status']
               == 'needs_reconciliation' for r in rig.rows)
    result = startup.execute(rig.broker, rig.account.identity_id, 'startup')
    assert result.ok and result.facts['attempted'] == result.changed == 3
    assert result.facts['skipped'] == 3 and result.facts['blocked'] == 0
    assert rig.downloads == [r.id for r in rig.rows[3:]]
    assert sql(rig.service, 'SELECT count(*) FROM artifacts') == [(6,)]
    assert len(rig.service.storage.placements()) == 9
    assert [p.read_bytes() for p in paths] == before
    assert sql(rig.service, 'SELECT count(*) FROM origin_links') == [(6,)]
    assert len(rig.service.storage.acquisitions()) == 9  # historical receipts retained
    again = rig.service.synchronize_account_source(rig.account)
    assert again['canonical_execution']['present_attested'] == 6
    assert len(rig.downloads) == 3


def test_unacquirable_purchase_does_not_starve_other_five_and_has_exact_reason(rig, caplog):
    authorize(rig)
    original = rig.session.rights_selected_delivery_offer_snapshot
    def offer(account, **kwargs):
        if kwargs['right_id'] == rig.rows[2].id:
            return replace(rig.snapshot, rights=(rig.snapshot.rights[2],))
        return original(account, **kwargs)
    rig.session.rights_selected_delivery_offer_snapshot = offer
    result = rig.service.synchronize_account_source(rig.account)
    assert result['canonical_execution']['downloaded'] == 5
    assert result['canonical_execution']['pending'] == 1
    assert len(rig.downloads) == 5
    assert len(rig.service.storage.entitlements()) == 6
    assert sql(rig.service, 'SELECT count(*) FROM source_references') == [(6,)]
    with caplog.at_level('INFO', logger='cifereca'):
        rig.service.synchronize_account_source(rig.account)
    assert any(rig.rows[2].id in r.message and 'delivery_unconfirmed' in r.message
               for r in caplog.records)


def test_disabled_automatic_never_offers_or_downloads(rig):
    result = rig.service.synchronize_account_source(rig.account)
    assert result['canonical_execution']['pending'] == 6
    assert not rig.downloads and not rig.offers
    authorize(rig)
    rig.broker.revoke_numilog_materialization_consent(rig.sid)
    rig.service.synchronize_account_source(rig.account)
    assert not rig.downloads and not rig.offers


def test_unknown_right_never_downloads_but_other_five_continue(rig):
    authorize(rig)
    rig.snapshot = replace(rig.snapshot, rights=(replace(rig.snapshot.rights[0], state='unknown'),
                                                *rig.snapshot.rights[1:]))
    result = rig.service.synchronize_account_source(rig.account)
    assert result['canonical_execution']['pending'] == 1
    assert result['canonical_execution']['downloaded'] == 5
    assert rig.rows[0].id not in rig.downloads


def test_two_profiles_same_sha_have_separate_rights_origins_and_placements(rig, tmp_path):
    other = install(rig.service, tmp_path, identity='alex', credential='numilog-other', count=1)
    other.contents[0] = rig.contents[0]
    authorize(rig)
    rig.service.synchronize_account_source(rig.account)
    rig.service.synchronize_account_source(other.account)
    assert not other.downloads and not other.offers
    authorize(other)
    rig.service.synchronize_account_source(other.account)
    assert len(other.downloads) == 1
    assert sql(rig.service, 'SELECT count(*) FROM artifacts') == [(6,)]
    assert len(rig.service.storage.placements()) == 7
    assert sql(rig.service, 'SELECT identity_id,count(*) FROM origin_links GROUP BY identity_id') == [('alex', 1), ('default', 6)]
    rig.service.synchronize_account_source(other.account)
    assert len(other.downloads) == 1


@pytest.mark.parametrize('reenable', [False, True])
def test_revoke_during_get_never_publishes_old_generation(rig, reenable):
    authorize(rig)
    def candidate(*args, **kwargs):
        result = rig.candidate(*args, **kwargs)
        rig.broker.revoke_numilog_materialization_consent(rig.sid)
        if reenable:
            authorize(rig)
        return result
    rig.session.rights_selected_delivery_candidate = candidate
    result = rig.service.synchronize_account_source(rig.account)
    assert result['canonical_execution']['blocked'] == 1
    assert len(rig.downloads) == 1
    assert sql(rig.service, 'SELECT count(*) FROM origin_links') == [(0,)]


def test_recovery_after_publication_interruption_never_repeats_get(rig, monkeypatch):
    authorize(rig)
    from cifereca.core import convergence_execution
    real = convergence_execution.publish_completed_acquisition
    def interrupted(*args, **kwargs):
        raise OSError('synthetic crash before SQLite commit')
    monkeypatch.setattr(convergence_execution, 'publish_completed_acquisition', interrupted)
    first = rig.service.synchronize_account_source(rig.account)
    assert first['canonical_execution']['failed'] == 6 and len(rig.downloads) == 6
    monkeypatch.setattr(convergence_execution, 'publish_completed_acquisition', real)
    second = rig.service.synchronize_account_source(rig.account)
    assert second['canonical_execution']['recovered'] == 6
    assert len(rig.downloads) == 6
    assert len(rig.service.storage.placements()) == 6


def test_restart_preserves_global_consent_and_presence(rig, tmp_path):
    authorize(rig)
    rig.service.synchronize_account_source(rig.account)
    rig.service.close()
    reopened = _service(tmp_path)
    try:
        state = install(reopened, tmp_path)
        result = reopened.synchronize_account_source(state.account)
        assert result['canonical_execution']['present_attested'] == 6
        assert not state.downloads and not state.offers
    finally:
        reopened.close()


def test_pending_delivery_resumes_offline_before_fallback_without_selector_dependency(rig):
    for i in range(3):
        seed(rig, i)
    historic_gets = []
    def old_delivery(item, directory):
        historic_gets.append(item.id)
        i = next(i for i, row in enumerate(rig.rows) if row.id == item.id)
        path = Path(directory) / ('acsm-pinned-%s' % i) / 'book.epub'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rig.contents[i])
        return path, 'https://synthetic.invalid/old-delivery'
    rig.session.download_direct = old_delivery
    for row in rig.rows[3:]:
        rig.service.acquire_direct_item(row)  # durable index_pending; no index
    assert len(historic_gets) == 3
    authorize(rig)
    rig.service.current_identity = rig.service.identity_by_id('alex')
    result = rig.service.synchronize_account_source(rig.account)
    assert result['canonical_execution']['present_attested'] == 6
    assert not rig.downloads and len(historic_gets) == 3
    assert all(r['status'] == 'complete' for r in rig.service.storage.acquisitions())


def test_global_and_manual_broker_share_canonical_delivery_and_no_second_get(rig, monkeypatch):
    from cifereca.plugin_api import SyncRequest
    authorize(rig)
    monkeypatch.setattr(rig.service, '_loans_for_account', lambda *_a, **_kw: [])
    opened = rig.service.opening_sync()
    own = next(row for row in opened['identities'] if row['identity_id'] == 'default')
    assert own['canonical_execution']['pending'] == 6
    assert not rig.downloads  # the independently queued inventory is not a second batch
    automatic = startup.execute(rig.broker, rig.account.identity_id, 'startup')
    assert automatic.facts['downloaded'] == 6
    assert len(rig.downloads) == 6
    manual = rig.broker.synchronize_source(rig.sid)
    assert manual.facts['library'] == 6
    rig.broker.synchronize(SyncRequest(identity_id='default'))
    assert len(rig.downloads) == 6


def test_global_consent_choices_do_not_require_individual_grants(rig):
    authorize(rig)
    rig.service.synchronize_account_source(rig.account)
    choices = rig.broker.canonical_acquisition_choices(rig.sid)
    assert len(choices) == 6
    assert all(c['allowed'] and c['consent_scope'] == 'source' for c in choices)
    rig.broker.revoke_numilog_materialization_consent(rig.sid)
    assert all(not c['allowed'] and 'consent_scope' not in c
               for c in rig.broker.canonical_acquisition_choices(rig.sid))


@pytest.mark.parametrize('changed', [False, True])
def test_declared_copy_stays_declared_and_stale_copy_does_not_veto_fallback(rig, changed):
    from cifereca.core.library_declared_links import declare_local_link
    from cifereca.core.library_inventory import scan_identity_roots
    root = rig.service.library_dir() / 'hugues'
    path = root / 'manual' / 'historical.epub'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rig.contents[0])
    observation, = scan_identity_roots({'default': (root,)}).files
    scope = dict(identity_id='default', provider_id='numilog',
                 credential_id=rig.account.credential_id, account_id=rig.account.unique_id,
                 item_id=rig.rows[0].id, observation=observation)
    plan = declare_local_link(rig.service, **scope)
    declare_local_link(rig.service, **scope, confirmation=plan.plan_token)
    if changed:
        path.write_bytes(b'changed historical copy')
    authorize(rig)
    result = rig.service.synchronize_account_source(rig.account)
    counts = result['canonical_execution']
    assert counts['downloaded'] == (6 if changed else 5)
    assert counts.get('present_declared', 0) == (0 if changed else 1)
    origins = sql(rig.service, 'SELECT count(*) FROM origin_links')
    assert origins == [(6 if changed else 5,)]
    assert path.is_file()


def test_startup_attempt_quota_leaves_remaining_rights_pending(tmp_path):
    service = _service(tmp_path)
    try:
        state = install(service, tmp_path, count=10)
        authorize(state)
        first = startup.execute(state.broker, state.account.identity_id, 'startup')
        assert first.facts['attempted'] == first.facts['downloaded'] == 8
        assert first.facts['blocked'] == 2 and first.facts['status'] == 'partial'
        assert len(state.downloads) == 8
        # Manual Sync is not the per-opening quota and completes the remainder.
        second = service.synchronize_account_source(state.account)
        assert second['canonical_execution']['downloaded'] == 2
        assert len(state.downloads) == 10
    finally:
        service.close()
