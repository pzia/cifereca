"""#7092 cache-only public port: no authority or private continuation leaks."""
from threading import Event

import pytest

from cifereca.core import local_origin_suggestion_batch as engine
from cifereca.core.local_origin_review_broker import LocalOriginBatchReadBrokerImpl
from cifereca.plugin_api.local_origin_review import LocalOriginBatchReadBroker, LocalOriginBatchPage
from tests.test_core_p0_3171_atomic_publication_v14 import store, rows
from tests.test_core_p0_4030_suggestion_operator import _service
from tests.test_core_p0_4030_suggestion_batch import _extra


def test_public_batch_page_is_scoped_and_cache_only(store, tmp_path):
    service, root, _ = _service(store, tmp_path)
    for index in range(4):
        _extra(store, root, f"more-{index}.pdf")
    broker = LocalOriginBatchReadBrokerImpl(service)
    assert isinstance(broker, LocalOriginBatchReadBroker)
    before = rows(store)
    assert broker.preview(identity_id="A") is None
    assert broker.preview(identity_id="B", confirmed=True) is None
    first = broker.preview(identity_id="A", confirmed=True)
    assert type(first) is LocalOriginBatchPage and first.page == 1
    assert len(first.entries) == 4 and first.can_continue
    assert not any((first.coverage_complete, first.provider_access_performed,
                    first.association_authorized))
    assert not hasattr(first, "next_cursor") and not hasattr(first, "scope")
    assert all(entry.status == "compared" and entry.known_matches == 1
               for entry in first.entries)
    assert broker.next_page(token=first.token) is None
    second = broker.next_page(token=first.token, confirmed=True)
    assert second.page == 2 and len(second.entries) == 1 and not second.can_continue
    assert second.token != first.token
    assert broker.next_page(token=first.token, confirmed=True) is None
    assert rows(store) == before


@pytest.mark.parametrize("change", ["epoch", "sql", "cancel", "revoke", "restart"])
def test_stale_token_never_enters_comparison(store, tmp_path, monkeypatch, change):
    service, root, _ = _service(store, tmp_path)
    for index in range(4):
        _extra(store, root, f"more-{index}.pdf")
    broker = LocalOriginBatchReadBrokerImpl(service)
    page = broker.preview(identity_id="A", confirmed=True)
    assert page.can_continue
    if change == "epoch":
        service._account_context_epoch += 2
    elif change == "sql":
        store.metadata_set("synthetic-change", "new")
    elif change == "cancel":
        broker.cancel(token=page.token)
    elif change == "revoke":
        broker.revoke()
    else:
        assert broker.preview(identity_id="A", confirmed=True).token != page.token

    monkeypatch.setattr(engine, "suggest_local_placement_documents",
                        lambda *a, **kw: pytest.fail("stale token searched"))
    assert broker.next_page(token=page.token, confirmed=True) is None


@pytest.mark.parametrize("change", ["cancel", "revoke"])
def test_mid_page_revocation_discards_result_and_never_mutates(
    store, tmp_path, monkeypatch, change,
):
    service, root, _ = _service(store, tmp_path)
    _extra(store, root, "another.pdf")
    before = rows(store)
    broker = LocalOriginBatchReadBrokerImpl(service)
    stop = Event()
    original = engine.suggest_local_placement_documents
    calls = []

    def observed(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(True)
        if change == "cancel":
            stop.set()
        else:
            broker.revoke()
        return result

    monkeypatch.setattr(engine, "suggest_local_placement_documents", observed)
    assert broker.preview(identity_id="A", confirmed=True, cancelled=stop.is_set) is None
    assert calls == [True] and rows(store) == before
    if change == "revoke":
        assert broker.preview(identity_id="A", confirmed=True) is None
        assert calls == [True]


def test_cancel_consumed_token_revokes_inflight_continuation(store, tmp_path, monkeypatch):
    service, root, _ = _service(store, tmp_path)
    for index in range(5):
        _extra(store, root, f"more-{index}.pdf")
    broker = LocalOriginBatchReadBrokerImpl(service)
    first = broker.preview(identity_id="A", confirmed=True)
    assert first.can_continue
    before = rows(store)
    original = engine.suggest_local_placement_documents
    calls = []

    def observed(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(True)
        broker.cancel(token=first.token)
        return result

    monkeypatch.setattr(engine, "suggest_local_placement_documents", observed)
    assert broker.next_page(token=first.token, confirmed=True) is None
    assert calls == [True] and rows(store) == before
    assert broker.next_page(token=first.token, confirmed=True) is None
