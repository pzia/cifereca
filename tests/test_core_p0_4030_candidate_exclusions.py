"""#4030: neither review path may treat private working files as candidates."""
from pathlib import Path

import pytest

from cifereca.core import local_origin_suggestion_batch as batch
from cifereca.core import local_origin_suggestion_preview as preview
from cifereca.core import local_origin_documentary_suggestions as documentary
from tests.test_core_p0_3171_atomic_publication_v14 import store, rows, BYTES
from tests.test_core_p0_4030_suggestion_operator import _service
from tests.test_core_p0_4030_suggestion_batch import _extra


@pytest.mark.parametrize("relative", [
    ".canonical-delivery/run/book.pdf", ".cifereca-staging/variants/book.pdf",
    "Acquisitions/.private/book.pdf", "Outbox/device/book.pdf",
    "Acquisitions/link/book.pdf", "Acquisitions/link-file.pdf",
])
def test_individual_and_batch_share_path_veto_before_hash_or_c_search(
    store, tmp_path, monkeypatch, relative,
):
    service, root, ordinary = _service(store, tmp_path)
    path = _extra(store, root, relative)
    pid = store.placement_for_path(path)["placement_id"]
    retained = path
    if relative == "Acquisitions/link/book.pdf":
        parent = path.parent
        external = tmp_path / "other-directory"
        parent.rename(external)
        parent.symlink_to(external, target_is_directory=True)
        retained = external / path.name
    elif relative == "Acquisitions/link-file.pdf":
        retained = tmp_path / "other-book.pdf"
        path.rename(retained)
        path.symlink_to(retained)
    before = rows(store)
    hashed, searched = [], []
    real_verify = preview._verified_variant_file
    real_search = documentary.suggest_known_public_documents

    def verify(p, *args, **kwargs):
        hashed.append(p)
        assert p != path
        return real_verify(p, *args, **kwargs)

    def search(*args, **kwargs):
        searched.append(True)
        return real_search(*args, **kwargs)

    monkeypatch.setattr(preview, "_verified_variant_file", verify)
    monkeypatch.setattr(documentary, "suggest_known_public_documents", search)
    scope = dict(library_root=root, identity_id="A", placement_id=pid)
    candidate = preview.preview_local_origin_suggestion(store, **scope)
    assert candidate["status"] == "excluded"
    assert not candidate["physical_bytes_verified"]
    suggestion = preview.suggest_local_placement_documents(
        store, explicitly_requested=True, **scope,
    )
    assert suggestion["status"] == "excluded" and suggestion["suggestions"] is None
    assert not hashed and not searched
    admitted = batch.admit_origin_batch(service, confirmed=True)
    page = batch.run_origin_batch(service, admitted)
    compared, = [x for x in page["entries"] if x["status"] == "compared"]
    excluded, = [x for x in page["entries"] if x["status"] == "not_assessed"]
    assert excluded["reason"] == "excluded_path" and excluded["known_matches"] is None
    assert excluded["file"] == "Fichier non vérifié"
    assert compared["known_matches"] == 1 and len(compared["suggestions"]) == 1
    ordinary_path = Path(store.placement_get(ordinary)["path"])
    assert hashed == [ordinary_path] and searched == [True]
    assert rows(store) == before and retained.read_bytes() == ordinary_path.read_bytes() == BYTES
    assert not page["may_write_origin_or_right"]


@pytest.mark.parametrize("field", ["identity_id", "placement_id"])
@pytest.mark.parametrize("spoof", ["\x85", "\u202e", "\u2028", "\u200d", "\u2066"])
def test_local_origin_scope_rejects_non_displayable_exact_ids_before_any_sql_or_hash(
    store, tmp_path, monkeypatch, field, spoof,
):
    service, root, pid = _service(store, tmp_path)
    path = Path(store.placement_get(pid)["path"])
    before, content = rows(store), path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("a spoofed exact scope must not reach SQLite")

    monkeypatch.setattr(preview, "_snapshot", forbidden)
    requested = {"identity_id": "A", "placement_id": pid}
    requested[field] += spoof + "FORGED"
    result = preview.preview_local_origin_suggestion(
        store, library_root=root, **requested,
    )
    assert result["status"] == "excluded" and result["reason"] == "invalid_scope"
    assert not result["physical_bytes_verified"] and not result["may_write_origin_or_right"]
    if field == "identity_id":
        assert preview.ordinary_suggestion_path(
            str(path), library_root=root, identity_id=requested[field],
            directory="A",
        ) is None
    assert rows(store) == before and path.read_bytes() == content


def test_safe_scope_identifiers_still_receive_read_only_preview(store, tmp_path):
    service, root, pid = _service(store, tmp_path)
    path = Path(store.placement_get(pid)["path"])
    assert preview._displayable_scope_id("A")
    assert preview._displayable_scope_id(pid)
    assert preview.ordinary_suggestion_path(
        str(path), library_root=root, identity_id="A", directory="A",
    ) == path
    before, content = rows(store), path.read_bytes()
    result = preview.preview_local_origin_suggestion(
        store, library_root=root, identity_id="A", placement_id=pid,
    )
    assert result["status"] == "documentary_candidate"
    assert result["physical_bytes_verified"] and not result["may_write_origin_or_right"]
    assert rows(store) == before and path.read_bytes() == content
