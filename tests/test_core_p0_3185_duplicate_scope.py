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
