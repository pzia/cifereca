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



def _record_declaration(store, path, *, identity="A", state="active", relation="a" * 32):
    """Exact #2797 wire fixture; no new supplier relation or OriginLink."""
    from cifereca.core.library_declared_links import _PREFIX, _json
    from cifereca.core.library_inventory import _fingerprint
    import hashlib
    record = {
        "version": 1, "relation_id": relation, "state": state,
        "origin": "user_declaration", "created_at": 1,
        "revoked_at": None if state == "active" else 2,
        "scope": {"identity_id": identity, "provider_id": "seller",
                  "credential_id": "card-A", "account_id": "buyer-A", "item_id": "edition"},
        "file": {"path": str(path), "size": path.stat().st_size,
                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                 "fingerprint": list(_fingerprint(path.stat()))},
        "right_state": {"entitlement_id": "right", "entitlement_type": "owned",
                        "state": "active", "loan_id": None, "expires_at": None},
    }
    store.metadata_set(_PREFIX + relation, _json(record))
    return record


@pytest.mark.parametrize("kind", ["path", "hardlink", "foreign", "same_sha", "revoked", "corrupt"])
def test_existing_declaration_is_distinct_from_attested_origin(store, tmp_path, kind):
    from cifereca.core.library_declared_links import _PREFIX
    import os
    root, path, pid, _ = _setup(store, tmp_path)
    other = path.with_name("another.pdf")
    if kind == "hardlink":
        os.link(path, other)
    elif kind == "same_sha":
        other.write_bytes(path.read_bytes())
    else:
        other = path
    if kind == "corrupt":
        store.metadata_set(_PREFIX + "a" * 32, "broken-json")
    else:
        _record_declaration(store, other, identity="B" if kind == "foreign" else "A",
                            state="revoked" if kind == "revoked" else "active")
    before = rows(store)
    normal = _request(store, root, pid)
    explicit = _request(store, root, pid, include_declared=True)
    if kind in ("path", "hardlink"):
        assert normal["reason"] == "declared_origin_requires_review"
        assert normal["suggestions"] is None
        assert explicit["reason"] == "declared_unverified_comparison"
        assert explicit["status"] == "partial" and explicit["suggestions"] is not None
        assert explicit["public_comparison_evidence"] is None
    elif kind in ("foreign", "corrupt"):
        assert normal["suggestions"] is None and explicit["suggestions"] is None
    else:
        assert normal["status"] == explicit["status"] == "partial"
        assert explicit["reason"] == "documentary_comparison_only"
    assert not explicit["association_authorized"] and not explicit["may_write_origin_or_right"]
    assert "buyer-A" not in repr(explicit) and str(root) not in repr(explicit)
    assert rows(store) == before and path.read_bytes() == BYTES


def test_declaration_created_during_search_revokes_page(store, tmp_path, monkeypatch):
    root, path, pid, _ = _setup(store, tmp_path)
    real = documentary.suggest_known_public_documents

    def changed(*args, **kwargs):
        result = real(*args, **kwargs)
        _record_declaration(store, path)
        return result

    monkeypatch.setattr(documentary, "suggest_known_public_documents", changed)
    outcome = _request(store, root, pid, include_declared=True)
    assert outcome["status"] == "excluded" and outcome["suggestions"] is None


@pytest.mark.parametrize("column", ["title", "author"])
def test_oversized_epub_catalogue_claim_cannot_be_treated_as_missing(
    store, tmp_path, monkeypatch, column,
):
    from tests.test_core_p0_4030_epub_evidence import _local_epub, META
    import hashlib

    root, path, pid = _local_epub(store, tmp_path, META)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    store.catalog_entry_put({
        "catalog_entry_id": "epub-oversized", "artifact_id": "sha256:" + digest,
        "placement_id": pid, "identity_id": "A", "provider_id": "local",
        "title": "Roman commun", "author": "Autrice", "local_state": "local",
    })
    with store._lock, store._connect() as db:
        db.execute(
            f"UPDATE catalog_entries SET {column}=? WHERE catalog_entry_id=?",
            ("x" * 2049, "epub-oversized"),
        )
    monkeypatch.setattr(
        documentary, "suggest_known_public_documents",
        lambda *_args, **_kwargs: pytest.fail("invalid catalogue must not query C"),
    )
    report = _request(store, root, pid)
    assert report["status"] == "excluded"
    assert report["reason"] == "catalogue_evidence_unavailable"
    assert report["suggestions"] is None
    assert not report["provider_access_performed"]
    assert path.is_file()


@pytest.mark.parametrize("drift", ["sha256", "size"])
def test_stale_user_declaration_on_same_path_is_not_applied_to_local_artifact(
    store, tmp_path, monkeypatch, drift,
):
    from cifereca.core.library_declared_links import _PREFIX, _json

    root, path, pid, _ = _setup(store, tmp_path)
    record = _record_declaration(store, path)
    if drift == "sha256":
        record["file"]["sha256"] = "0" * 64
    else:
        record["file"]["size"] += 1
    store.metadata_set(_PREFIX + record["relation_id"], _json(record))
    prior = rows(store)
    monkeypatch.setattr(
        documentary, "suggest_known_public_documents",
        lambda *_args, **_kwargs: pytest.fail("stale declaration must block C"),
    )
    for selected in (False, True):
        report = _request(store, root, pid, include_declared=selected)
        assert report["status"] == "excluded"
        assert report["reason"] == "declaration_scope_unverified"
        assert report["suggestions"] is None
        assert not report["association_authorized"]
    assert rows(store) == prior
    assert path.read_bytes() == BYTES


@pytest.mark.parametrize("field", ["inode", "mtime", "ctime"])
def test_declared_origin_must_match_exact_pinned_file_fingerprint(
    store, tmp_path, monkeypatch, field,
):
    from cifereca.core.library_declared_links import _PREFIX, _json

    root, path, pid, _ = _setup(store, tmp_path)
    declaration = _record_declaration(store, path)
    index = {"inode": 1, "mtime": 3, "ctime": 4}[field]
    declaration["file"]["fingerprint"][index] += 1
    store.metadata_set(_PREFIX + declaration["relation_id"], _json(declaration))
    before = rows(store)
    monkeypatch.setattr(
        documentary, "suggest_known_public_documents",
        lambda *_args, **_kwargs: pytest.fail("stale file selection must not reach C"),
    )
    ordinary = _request(store, root, pid)
    assert ordinary["status"] == "excluded"
    assert ordinary["reason"] == "declared_origin_requires_review"

    confirmed = _request(store, root, pid, include_declared=True)
    assert confirmed["status"] == "excluded"
    assert confirmed["reason"] == "declaration_scope_unverified"
    assert confirmed["suggestions"] is None
    assert not confirmed["association_authorized"]
    assert not confirmed["may_write_origin_or_right"]
    assert rows(store) == before and path.read_bytes() == BYTES
