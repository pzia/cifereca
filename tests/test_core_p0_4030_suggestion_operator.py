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
