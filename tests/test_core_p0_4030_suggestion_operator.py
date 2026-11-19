"""#4030: queued operator previews cannot cross profile/config generations."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core import local_origin_suggestion_operator as operator
from tests.test_core_p0_3171_atomic_publication_v14 import store
from tests.test_core_p0_4030_scoped_documentary_suggestions import _setup


class Config:
    def __init__(self, root):
        self.root = root

    def get(self, key, default=None):
        return str(self.root) if key == "library.directory" else default

    def identities(self):
        return [{"id": "A", "directory_name": "A"}]


def _service(store, tmp_path):
    root, path, pid, entry = _setup(store, tmp_path)
    owner = NS(identity_id="A", directory_name="A")
    service = NS(storage=store, config=Config(root), current_identity=owner,
                 identity_by_id=lambda identity: owner if identity == "A" else None,
                 _source_context_epoch=1, _account_context_epoch=1)
    return service, root, pid


def _admit(service, pid, **kwargs):
    return operator.admit_local_origin_suggestion(
        service, identity_id="A", placement_id=pid,
        **{"confirmed": True, **kwargs},
    )


def test_valid_request_only_reads_known_c(store, tmp_path):
    service, root, pid = _service(store, tmp_path)
    request = _admit(service, pid)
    assert request is not None
    outcome = operator.run_local_origin_suggestion(service, request)
    assert outcome["status"] == "partial"
    assert len(outcome["suggestions"].suggestions) == 1
    assert not outcome["association_authorized"]
    assert not outcome["provider_access_performed"]
    assert pid not in repr(request) and str(root) not in repr(request)


@pytest.mark.parametrize("change", ["source", "navigation", "owner", "root", "database", "service"])
def test_revoked_queued_request_never_runs(store, tmp_path, monkeypatch, change):
    service, root, pid = _service(store, tmp_path)
    request = _admit(service, pid)
    assert request is not None
    if change == "source":
        service._source_context_epoch += 1
    elif change == "navigation":
        service._account_context_epoch += 2
    elif change == "owner":
        service.current_identity = NS(identity_id="A", directory_name="A")
    elif change == "root":
        service.config.root = tmp_path / "other-root"
    elif change == "database":
        service.storage = NS(path=store.path)
    else:
        service = NS(**vars(service))
    monkeypatch.setattr(operator, "suggest_local_placement_documents",
                        lambda *a, **k: pytest.fail("revoked request must not search"))
    result = operator.run_local_origin_suggestion(service, request)
    assert result["reason"] == "scope_changed" and result["suggestions"] is None
    assert not (tmp_path / "other-root").exists()


def test_generation_change_during_search_discards_result(store, tmp_path, monkeypatch):
    service, root, pid = _service(store, tmp_path)
    request = _admit(service, pid)
    original = operator.suggest_local_placement_documents

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        service._source_context_epoch += 1
        return result

    monkeypatch.setattr(operator, "suggest_local_placement_documents", changed)
    result = operator.run_local_origin_suggestion(service, request)
    assert result["reason"] == "scope_changed" and result["suggestions"] is None


def test_no_implicit_confirmation_or_directory_creation(store, tmp_path):
    service, root, pid = _service(store, tmp_path)
    service.config.root = tmp_path / "absent"
    assert _admit(service, pid, confirmed=False) is None
    assert not service.config.root.exists()


def _more_public(store, count=2):
    from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public
    for i in range(count):
        persist(store, verified_public(record_id=f"public-page-{i}",
                                       title="Roman commun", author="Autrice"), at=100)


def test_successive_explicit_pages_preserve_private_local_witness(store, tmp_path):
    service, root, pid = _service(store, tmp_path)
    _more_public(store)
    request = _admit(service, pid, page_size=1)
    seen = []
    for page_index in range(3):
        assert request.page == page_index
        outcome = operator.run_local_origin_suggestion(service, request)
        page = outcome["suggestions"]
        assert page.page == page_index and page.page_size == 1
        assert page.coverage_complete is False
        assert not outcome["association_authorized"]
        seen.append(page.suggestions[0].hit.record_id)
        assert pid not in repr(outcome) and str(root) not in repr(outcome)
        request = operator.continue_local_origin_suggestion(service, request, outcome)
    assert len(set(seen)) == 3 and request is None


@pytest.mark.parametrize("change", ["metadata", "bytes", "epoch", "unadmitted_page"])
def test_continuation_cannot_retarget_or_mix_local_evidence(store, tmp_path, monkeypatch, change):
    from cifereca.core import local_origin_documentary_suggestions as documentary
    service, root, pid = _service(store, tmp_path)
    _more_public(store)
    request = _admit(service, pid, page_size=1)
    first = operator.run_local_origin_suggestion(service, request)
    next_request = operator.continue_local_origin_suggestion(service, request, first)
    assert next_request is not None
    if change == "metadata":
        with store._lock, store._connect() as db:
            db.execute("UPDATE catalog_entries SET title='Autre roman' WHERE placement_id=?", (pid,))
    elif change == "bytes":
        path = store.placement_get(pid)["path"]
        from pathlib import Path
        Path(path).write_bytes(b"changed")
    elif change == "epoch":
        service._account_context_epoch += 2
    else:
        from dataclasses import replace
        next_request = replace(next_request, continuation=None)
        assert _admit(service, pid, page=1) is None
    monkeypatch.setattr(documentary, "suggest_known_public_documents",
                        lambda *_a, **_kw: pytest.fail("invalid continuation must not search"))
    outcome = operator.run_local_origin_suggestion(service, next_request)
    assert outcome["suggestions"] is None and outcome["continuation"] is None
