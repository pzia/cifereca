"""Safety regressions for source-scoped library maintenance."""
import shutil
import pytest
from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.plugin_api import Conflict, LibraryMutationRequest
from tests.test_core041_artifact_authority import make_service

@pytest.mark.parametrize('changed', ['identity_id', 'provider_id', 'source_item_id'])
def test_cross_scope_duplicate_plan_refused(tmp_path, changed):
    service, artifact, original = make_service(tmp_path)
    candidate = tmp_path / 'candidate.epub'
    shutil.copyfile(artifact.path, candidate)
    attributes = dict(artifact_id=artifact.artifact_id, path=str(candidate), identity_id='id1', provider_id='local', source_item_id='book-1', state='present')
    attributes[changed] = 'different'
    duplicate = service.storage.placement_put(attributes)
    request = LibraryMutationRequest('delete_duplicate', placement_id=duplicate, keep_placement_id=original)
    with pytest.raises(Conflict):
        LibraryMaintenanceBrokerImpl(service).plan(request)
    assert candidate.is_file() and artifact.path.is_file()
    assert service.storage.placement_get(duplicate) is not None

def test_changed_placement_scope_between_plan_and_run_is_refused(tmp_path):
    service, artifact, original = make_service(tmp_path)
    candidate = tmp_path / 'candidate.epub'
    shutil.copyfile(artifact.path, candidate)
    second = service.storage.placement_put(dict(
        artifact_id=artifact.artifact_id, path=str(candidate),
        identity_id='id1', provider_id='local', source_item_id='book-1',
        state='present',
    ))
    request = LibraryMutationRequest('delete_duplicate', placement_id=second,
                                     keep_placement_id=original)
    broker = LibraryMaintenanceBrokerImpl(service)
    plan = broker.plan(request)
    with service.storage._lock, service.storage._connect() as db:
        db.execute("UPDATE placements SET identity_id='id2' WHERE placement_id=?", (second,))
    with pytest.raises(Conflict):
        broker.run(plan)
    assert candidate.is_file() and artifact.path.is_file()


def test_provider_edition_with_two_recorded_buyers_cannot_be_coalesced(tmp_path):
    service, artifact, original = make_service(tmp_path)
    candidate = tmp_path / 'candidate.epub'
    shutil.copyfile(artifact.path, candidate)
    second = service.storage.placement_put(dict(
        artifact_id=artifact.artifact_id, path=str(candidate),
        identity_id='id1', provider_id='local', source_item_id='book-1',
        state='present',
    ))
    with service.storage._lock, service.storage._connect() as db:
        db.execute("UPDATE placements SET provider_id='numilog' WHERE placement_id IN (?, ?)",
                   (original, second))
    for buyer in ('a', 'b'):
        service.storage.entitlement_put(dict(
            identity_id='id1', provider_id='numilog',
            credential_id='credential-' + buyer, account_id='account-' + buyer,
            item_id='book-1', entitlement_type='owned',
            state='active',
        ))
    broker = LibraryMaintenanceBrokerImpl(service)
    request = LibraryMutationRequest('delete_duplicate', placement_id=second,
                                     keep_placement_id=original)
    with pytest.raises(Conflict):
        broker.plan(request)
    assert candidate.is_file() and artifact.path.is_file()


def test_matching_local_scope_preserves_existing_maintenance_contract(tmp_path):
    service, artifact, original = make_service(tmp_path)
    candidate = tmp_path / 'candidate.epub'
    shutil.copyfile(artifact.path, candidate)
    second = service.storage.placement_put(dict(
        artifact_id=artifact.artifact_id, path=str(candidate),
        identity_id='id1', provider_id='local', source_item_id='book-1',
        state='present',
    ))
    broker = LibraryMaintenanceBrokerImpl(service)
    request = LibraryMutationRequest('delete_duplicate', placement_id=second,
                                     keep_placement_id=original)
    plan = broker.plan(request)
    outcome = broker.run(plan)
    assert outcome.ok and not candidate.exists()
    assert artifact.path.is_file()
