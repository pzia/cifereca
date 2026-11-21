"""#7092 contextual port over real SQLite/local evidence; no provider calls."""
import pytest

from cifereca.core import local_origin_detail_broker as module
from cifereca.core.activation_scoped_broker import ActivationScopedReadFactory
from cifereca.plugin_api.local_origin_detail import (
    LocalOriginDetailPage, LocalOriginDetailReadBroker,
)
from tests.test_core_p0_3171_atomic_publication_v14 import store, rows
from tests.test_core_p0_4030_suggestion_operator import _service, _more_public


def fixture(store, tmp_path):
    service, root, pid = _service(store, tmp_path)
    _more_public(store, count=2)
    return service, root, pid


def preview(broker, pid, *, view=lambda: True, identity="A", confirmed=True):
    return broker.preview(identity_id=identity, placement_id=pid,
                          still_current=view, confirmed=confirmed, page_size=1)


def test_exact_contextual_pages_preserve_public_editions_and_private_state(store, tmp_path):
    service, root, pid = fixture(store, tmp_path)
    broker = module.LocalOriginDetailReadBrokerImpl(service)
    assert isinstance(broker, LocalOriginDetailReadBroker)
    before = rows(store)
    assert preview(broker, pid, confirmed=False) is None
    assert preview(broker, pid, identity="B") is None
    assert preview(broker, "missing-placement") is None
    page = preview(broker, pid)
    records = set()
    for index in range(1, 4):
        assert type(page) is LocalOriginDetailPage and page.page == index
        assert page.known_matches == 3 and len(page.matches) == 1
        assert not (page.coverage_complete or page.provider_access_performed
                    or page.association_authorized)
        hit = page.matches[0]
        assert hit.record_id and hit.provider_id and hit.title == "Roman commun"
        records.add(hit.record_id)
        assert not hasattr(page, "continuation") and not hasattr(page, "request")
        assert str(root) not in repr(page) and pid not in repr(page)
        if index < 3:
            assert page.can_continue
            old = page.token
            assert broker.next_page(token=old) is None
            page = broker.next_page(token=old, confirmed=True)
            assert broker.next_page(token=old, confirmed=True) is None
        else:
            assert not page.can_continue
            assert broker.next_page(token=page.token, confirmed=True) is None
    assert len(records) == 3 and rows(store) == before


@pytest.mark.parametrize("change", ["view", "epoch", "revoke", "cancel", "new_preview"])
def test_retired_context_never_reenters_core(store, tmp_path, monkeypatch, change):
    service, root, pid = fixture(store, tmp_path)
    broker = module.LocalOriginDetailReadBrokerImpl(service)
    live = [True]
    first = preview(broker, pid, view=lambda: live[0])
    assert first.can_continue
    if change == "view":
        live[0] = False
    elif change == "epoch":
        service._account_context_epoch += 2
    elif change == "revoke":
        broker.revoke()
    elif change == "cancel":
        broker.cancel(token=first.token)
    else:
        assert preview(broker, pid).token != first.token

    monkeypatch.setattr(module, "run_local_origin_suggestion",
                        lambda *_a, **_kw: pytest.fail("stale context reached Core"))
    assert broker.next_page(token=first.token, confirmed=True) is None


@pytest.mark.parametrize("change", ["view", "activation"])
def test_result_revoked_during_worker_never_crosses_port(store, tmp_path, monkeypatch, change):
    service, root, pid = fixture(store, tmp_path)
    before = rows(store)
    active, view = [True], [True]
    factory = ActivationScopedReadFactory(
        lambda: module.LocalOriginDetailReadBrokerImpl(service),
        ("preview", "next_page", "cancel"),
    )
    broker = factory.for_activation("detail.fixture", current=lambda: active[0])
    actual = module.run_local_origin_suggestion

    def changed(*args):
        value = actual(*args)
        if change == "view":
            view[0] = False
        else:
            active[0] = False
        return value

    monkeypatch.setattr(module, "run_local_origin_suggestion", changed)
    assert not hasattr(broker, "storage") and not hasattr(broker, "revoke")
    assert preview(broker, pid, view=lambda: view[0]) is None
    assert rows(store) == before
