"""#4030: batch reuses real local and public-C evidence, without mutations."""
from pathlib import Path
from threading import Event

import pytest

from cifereca.core import local_origin_suggestion_batch as batch
from cifereca.gui.local_origin_suggestion_batch_line import _display
from tests.test_core_p0_3171_atomic_publication_v14 import (
    store, register, new_source, publish, target, rows, BYTES, SHA,
)
from tests.test_core_p0_4030_suggestion_operator import _service


def _extra(store, root, name):
    path = root / "A" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(BYTES)
    pid = store.placement_put({"artifact_id": "sha256:" + SHA,
                               "identity_id": "A", "path": str(path), "state": "present"})
    store.catalog_entry_put({
        "catalog_entry_id": "test-" + pid, "artifact_id": "sha256:" + SHA,
        "placement_id": pid, "identity_id": "A", "provider_id": "local",
        "title": "Roman commun", "author": "Autrice", "local_state": "local",
    })
    return path


def test_batch_pages_preserve_scope_and_distinguish_unassessed_candidates(store, tmp_path):
    service, root, pid = _service(store, tmp_path)
    files = [Path(store.placement_get(pid)["path"])]
    files += [_extra(store, root, f"book-{i}.pdf") for i in range(4)]
    hidden = _extra(store, root, ".cifereca-staging/hidden.pdf")
    damaged = _extra(store, root, "damaged.pdf")
    damaged.write_bytes(b"changed")
    # A confirmed provider origin in B, even with the same SHA, cannot
    # contaminate the unlinked candidates that actually belong to A.
    register(store, "B", "other", "card-B")
    source, snapshot = new_source("B", provider="other", credential="card-B")
    foreign = target(tmp_path, "B")
    publish(store, root, foreign, source, snapshot)
    before = rows(store)
    assert batch.admit_origin_batch(service) is None
    request = batch.admit_origin_batch(service, confirmed=True)
    first = batch.run_origin_batch(service, request)
    assert first["status"] == "partial" and first["page"] == 1
    assert len(first["entries"]) == 4 and first["has_more"]
    second = batch.run_origin_batch(service, request, cursor=first["next_cursor"])
    assert second["page"] == 2 and second["next_cursor"] is None and not second["has_more"]
    all_rows = first["entries"] + second["entries"]
    assert len(all_rows) == 7
    matches = [x for x in all_rows if x["status"] == "compared"]
    assert {x["file"] for x in matches} == {p.name for p in files}
    assert all(x["known_matches"] == 1 and len(x["suggestions"]) == 1 for x in matches)
    not_assessed = [x for x in all_rows if x["status"] == "not_assessed"]
    assert len(not_assessed) == 2 and all(x["known_matches"] is None for x in not_assessed)
    for page in (first, second):
        assert not page["association_authorized"] and not page["coverage_complete"]
        assert not page["provider_access_performed"] and not page["may_write_origin_or_right"]
        assert str(root) not in repr(page) and SHA not in repr(page)
        assert "couverture partielle" in _display(page)
    assert "Non évalué" in _display(first) + _display(second)
    assert rows(store) == before
    assert all(p.read_bytes() == BYTES for p in files + [hidden, foreign])
    assert damaged.read_bytes() == b"changed"


@pytest.mark.parametrize("mutation", ["epoch", "new_scope", "database"])
def test_cursor_cannot_cross_scope_or_changed_database(store, tmp_path, monkeypatch, mutation):
    service, root, _ = _service(store, tmp_path)
    for i in range(4):
        _extra(store, root, f"more-{i}.pdf")
    scope = batch.admit_origin_batch(service, confirmed=True)
    first = batch.run_origin_batch(service, scope)
    cursor = first["next_cursor"]
    assert cursor is not None
    if mutation == "epoch":
        service._account_context_epoch += 2
    elif mutation == "new_scope":
        scope = batch.admit_origin_batch(service, confirmed=True)
    else:
        store.metadata_set("synthetic.batch_change", "changed")
    monkeypatch.setattr(batch, "suggest_local_placement_documents",
                        lambda *_a, **_kw: pytest.fail("revoked cursor searched"))
    assert batch.run_origin_batch(service, scope, cursor=cursor) is None


@pytest.mark.parametrize("change", ["cancel", "epoch", "database"])
def test_mid_batch_change_discards_partial_result(store, tmp_path, monkeypatch, change):
    service, root, _ = _service(store, tmp_path)
    _extra(store, root, "another.pdf")
    scope = batch.admit_origin_batch(service, confirmed=True)
    stop = Event()
    actual = batch.suggest_local_placement_documents
    calls = 0

    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = actual(*args, **kwargs)
        if change == "cancel":
            stop.set()
        elif change == "epoch":
            service._source_context_epoch += 1
        else:
            store.metadata_set("synthetic.batch_change", "changed")
        return result

    monkeypatch.setattr(batch, "suggest_local_placement_documents", changed)
    assert batch.run_origin_batch(service, scope, cancelled=stop.is_set) is None
    assert calls == 1


def test_budget_exclusion_is_not_a_negative_match(store, tmp_path, monkeypatch):
    service, root, _ = _service(store, tmp_path)
    scope = batch.admit_origin_batch(service, confirmed=True)
    monkeypatch.setattr(batch, "_MAX_BYTES", 1)
    monkeypatch.setattr(batch, "suggest_local_placement_documents",
                        lambda *_a, **_kw: pytest.fail("over-budget search"))
    page = batch.run_origin_batch(service, scope)
    row, = page["entries"]
    assert row["reason"] == "review_budget" and row["known_matches"] is None
    assert row["status"] == "not_assessed"
    assert "Non évalué" in _display(page)


@pytest.mark.parametrize("failure", [OSError("connection gone"), RuntimeError("profile rebound")])
def test_batch_binding_failure_closes_direct_scope_and_public_page(
    store, tmp_path, monkeypatch, failure,
):
    service, root, pid = _service(store, tmp_path)
    scope = batch.admit_origin_batch(service, confirmed=True)
    assert scope is not None and scope.bound()

    def unreachable_binding(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(batch, "_binding_current", unreachable_binding)
    monkeypatch.setattr(batch, "suggest_local_placement_documents",
                        lambda *_args, **_kwargs: pytest.fail("no C query on invalid scope"))
    assert scope.bound() is False
    assert batch.run_origin_batch(service, scope) is None
    assert Path(store.placement_get(pid)["path"]).is_file()



def test_batch_explicit_origin_filter_preserves_declared_statement(store, tmp_path):
    from cifereca.core.local_origin_review_broker import LocalOriginBatchReadBrokerImpl
    from tests.test_core_p0_4030_scoped_documentary_suggestions import _record_declaration
    service, root, pid = _service(store, tmp_path)
    path = Path(store.placement_get(pid)["path"])
    _record_declaration(store, path)
    before = rows(store)
    broker = LocalOriginBatchReadBrokerImpl(service)
    assert broker.preview(identity_id="A", confirmed=True, include_declared=1) is None
    normal = broker.preview(identity_id="A", confirmed=True)
    explicit = broker.preview(identity_id="A", confirmed=True, include_declared=True)
    (excluded,) = normal.entries
    (included,) = explicit.entries
    assert excluded.status == "not_assessed"
    assert excluded.reason == "declared_origin_requires_review"
    assert included.status == "compared" and included.reason == "declared_unverified_comparison"
    assert included.known_matches == 1
    assert not explicit.association_authorized and not explicit.provider_access_performed
    assert rows(store) == before and path.read_bytes() == BYTES


@pytest.mark.parametrize("change", ["bytes", "inode", "missing"])
def test_previously_compared_file_changed_during_later_batch_row_is_not_delivered(
        store, tmp_path, monkeypatch, change):
    service, root, pid = _service(store, tmp_path)
    _extra(store, root, "second.pdf")
    scope = batch.admit_origin_batch(service, confirmed=True)
    actual = batch.suggest_local_placement_documents
    visited = []

    def changed(*args, **kwargs):
        result = actual(*args, **kwargs)
        visited.append(Path(store.placement_get(kwargs["placement_id"])["path"]))
        if len(visited) == 2:
            first = visited[0]
            if change == "bytes":
                first.write_bytes(b"changed")
            elif change == "inode":
                replacement = first.with_suffix(".replacement")
                replacement.write_bytes(first.read_bytes())
                replacement.replace(first)
            else:
                first.unlink()
        return result

    before = rows(store)
    monkeypatch.setattr(batch, "suggest_local_placement_documents", changed)
    assert batch.run_origin_batch(service, scope) is None
    assert len(visited) == 2 and rows(store) == before
