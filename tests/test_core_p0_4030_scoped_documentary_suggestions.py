"""#4030: one explicit local placement, known-C only, revalidated on return."""
import pytest

from cifereca.core import local_origin_suggestion_preview as preview
from cifereca.core import local_origin_documentary_suggestions as documentary
from tests.test_core_p0_3171_atomic_publication_v14 import (
    store, register, new_source, publish, rows, BYTES, SHA,
)
from tests.test_core_p0_4030_local_origin_suggestion_preview import _local
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


def _setup(store, tmp_path):
    root = tmp_path / "EBooks"
    register(store, "A", "seller", "card-A")
    path, pid = _local(store, root, "A")
    entry = {
        "catalog_entry_id": "synthetic-local", "artifact_id": "sha256:" + SHA,
        "placement_id": pid, "identity_id": "A", "provider_id": "local",
        "title": "Roman commun", "author": "Autrice", "local_state": "local",
    }
    store.catalog_entry_put(entry)
    persist(store, verified_public(record_id="public-record", title="Roman commun",
                                   author="Autrice"), at=100)
    return root, path, pid, entry


def _request(store, root, pid, **kwargs):
    options = {"explicitly_requested": True, **kwargs}
    return preview.suggest_local_placement_documents(
        store, library_root=root, identity_id="A", placement_id=pid, **options,
    )


def test_explicit_private_preview_returns_unattached_public_suggestions(store, tmp_path):
    root, path, pid, _ = _setup(store, tmp_path)
    before = rows(store)
    result = _request(store, root, pid)
    assert result["status"] == "partial"
    assert result["metadata_evidence"] == "local_catalogue_only"
    hit, = result["suggestions"].suggestions
    assert hit.comparison == "title_author_concordant"
    assert hit.hit.record_id == "public-record"
    assert not result["association_authorized"] and not result["provider_access_performed"]
    assert rows(store) == before and path.read_bytes() == BYTES
    assert pid not in repr(result) and str(root) not in repr(result)


@pytest.mark.parametrize("change", ["no_request", "missing", "conflict", "foreign", "no_metadata"])
def test_no_search_without_exact_evidence(store, tmp_path, monkeypatch, change):
    root, path, pid, entry = _setup(store, tmp_path)
    requested = True
    if change == "no_request":
        requested = False
    elif change == "missing":
        path.unlink()
    elif change == "conflict":
        store.catalog_entry_put({**entry, "catalog_entry_id": "conflict",
                                 "title": "Autre roman"})
    elif change == "foreign":
        with store._lock, store._connect() as db:
            db.execute("UPDATE catalog_entries SET identity_id='B' WHERE catalog_entry_id='synthetic-local'")
    else:
        with store._lock, store._connect() as db:
            db.execute("DELETE FROM catalog_entries WHERE catalog_entry_id='synthetic-local'")
    monkeypatch.setattr(documentary, "suggest_known_public_documents",
                        lambda *a, **k: pytest.fail("must not search"))
    report = _request(store, root, pid, explicitly_requested=requested)
    assert report["status"] == "excluded" and report["suggestions"] is None


@pytest.mark.parametrize("change", ["new_origin", "metadata", "bytes"])
def test_discard_entire_page_if_evidence_changed_during_search(store, tmp_path, monkeypatch, change):
    root, path, pid, entry = _setup(store, tmp_path)
    original = documentary.suggest_known_public_documents

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == "new_origin":
            source, snap = new_source()
            publish(store, root, path, source, snap)
        elif change == "metadata":
            store.catalog_entry_put({**entry, "title": "Nouveau titre"})
        else:
            path.write_bytes(b"new bytes")
        return result

    monkeypatch.setattr(documentary, "suggest_known_public_documents", changed)
    report = _request(store, root, pid)
    assert report["status"] == "excluded"
    assert report["suggestions"] is None



@pytest.mark.parametrize("column,sql_value", [
    ("title", b"Roman commun"), ("author", b"Autrice"),
    ("title", b"42"), ("author", b"42"),
])
def test_non_native_catalogue_text_never_dispatches_known_c(
    store, tmp_path, monkeypatch, column, sql_value,
):
    root, path, pid, _ = _setup(store, tmp_path)
    original_bytes = path.read_bytes()
    with store._lock, store._connect() as db:
        db.execute(
            f"UPDATE catalog_entries SET {column}=? WHERE catalog_entry_id=?",
            (sql_value, "synthetic-local"),
        )
    monkeypatch.setattr(
        documentary, "suggest_known_public_documents",
        lambda *_args, **_kwargs: pytest.fail("untrusted catalogue never queries C"),
    )
    report = _request(store, root, pid)
    assert report["status"] == "excluded"
    assert report["reason"] == "catalogue_evidence_unavailable"
    assert report["suggestions"] is None
    assert not report["provider_access_performed"]
    assert path.read_bytes() == original_bytes


@pytest.mark.parametrize("column", ["title", "author"])
def test_non_native_epub_catalogue_text_is_not_silently_ignored(
    store, tmp_path, monkeypatch, column,
):
    from tests.test_core_p0_4030_epub_evidence import _local_epub, META

    root, path, pid = _local_epub(store, tmp_path, META)
    digest = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    store.catalog_entry_put({
        "catalog_entry_id": "epub-local", "artifact_id": "sha256:" + digest,
        "placement_id": pid, "identity_id": "A", "provider_id": "local",
        "title": "Roman commun", "author": "Autrice", "local_state": "local",
    })
    with store._lock, store._connect() as db:
        db.execute(
            f"UPDATE catalog_entries SET {column}=? WHERE catalog_entry_id=?",
            (b"corrupt-binary", "epub-local"),
        )
    monkeypatch.setattr(
        documentary, "suggest_known_public_documents",
        lambda *_args, **_kwargs: pytest.fail("untrusted catalogue never queries C"),
    )
    report = _request(store, root, pid)
    assert report["status"] == "excluded"
    assert report["reason"] == "catalogue_evidence_unavailable"
    assert path.is_file()
