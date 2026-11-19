"""#2835: a library rename is not an EPUB reader-engine conversion."""
from dataclasses import replace
import os

import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.plugin_api import Conflict, LibraryMutationRequest
from tests.test_core041_artifact_authority import Control, make_service


def _case(tmp_path, suffix):
    service, artifact, placement = make_service(tmp_path)
    initial = tmp_path / "library" / "Book.epub"
    old = initial.with_name("Book" + suffix)
    if initial != old:
        os.link(initial, old)
        service.storage.relocate_local_path(
            initial, old, identity_id="id1", provider_id="local",
        )
        initial.unlink()
    broker = LibraryMaintenanceBrokerImpl(service)
    request = LibraryMutationRequest("normalize_filename", placement_id=placement)
    return service, artifact, placement, old, broker, request


@pytest.mark.parametrize("suffix", [".epub", ".kepub.epub", ".fxl.kepub.epub", ".FXL.KePub.EPUB"])
def test_rename_preserves_exact_suffix_and_canonical_artifact(tmp_path, suffix):
    service, artifact, placement, old, broker, request = _case(tmp_path, suffix)
    try:
        plan = broker.plan(request)
        new = old.with_name("Author - Book" + suffix)
        assert plan.changes == (old.name, new.name)
        assert plan.facts["new_filename"] == new.name
        result = broker.run(plan, Control())
        assert result.ok and result.changed == 1
        assert new.read_bytes() == b"book-data" and not old.exists()
        row = service.storage.placement_get(placement)
        assert row["path"] == str(new) and row["artifact_id"] == artifact.artifact_id
        assert service.storage.artifact_get(artifact.artifact_id)["sha256"] == artifact.sha256
    finally:
        service.storage.close()


@pytest.mark.parametrize("source,target", [
    (".fxl.kepub.epub", ".epub"),
    (".fxl.kepub.epub", ".kepub.epub"),
    (".kepub.epub", ".fxl.kepub.epub"),
    (".epub", ".kepub.epub"),
    (".FXL.KEPUB.EPUB", ".fxl.kepub.epub"),
])
def test_stale_or_forged_plan_cannot_change_convention(tmp_path, source, target):
    service, artifact, placement, old, broker, request = _case(tmp_path, source)
    try:
        plan = broker.plan(request)
        new = old.with_name("Author - Book" + target)
        plan = replace(plan, changes=(old.name, new.name), facts={"new_filename": new.name})
        with pytest.raises(Conflict) as caught:
            broker.run(plan, Control())
        assert caught.value.code == "library_filename_conflict"
        assert old.read_bytes() == b"book-data" and not new.exists()
        assert service.storage.placement_get(placement)["path"] == str(old)
        assert service.storage.artifact_get(artifact.artifact_id)["sha256"] == artifact.sha256
    finally:
        service.storage.close()


def test_naming_policy_cannot_accidentally_propose_a_new_engine(tmp_path):
    service, _, placement, old, broker, request = _case(tmp_path, ".epub")
    try:
        service.preview_library_filename = lambda **_kw: "Book.fxl.kepub.epub"
        with pytest.raises(Conflict) as caught:
            broker.plan(request)
        assert caught.value.code == "library_filename_conflict"
        assert old.exists() and service.storage.placement_get(placement)["path"] == str(old)
    finally:
        service.storage.close()
