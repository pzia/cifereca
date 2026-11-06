"""#4664: loaded Search facts can be mutated in place without rebinding a row.

A stable Python identity, provider, credential and Search list key alone are
not sufficient to publish actions for an edition whose nested facts changed.
Fixtures never access the network or perform supplier mutations.
"""

from types import SimpleNamespace as NS
import pytest

from cifereca.gui import recommendation_exact_detail as port
from cifereca.plugin_api import Cancelled
from tests.test_core_p1_4664_recommendation_exact_detail import _fixture, _start


@pytest.mark.parametrize("change_point", [
    "before_work", "during_get", "before_delivery",
])
def test_nested_loaded_notice_evidence_mutation_revokes_actions(
    monkeypatch, change_point,
):
    f = _fixture(monkeypatch)
    f.item.metadata = {
        "known_catalog": {"edition_revision": "r1",
                          "offer": {"available": True}},
    }
    work, success, failure = _start(f)

    def edit_offer():
        f.item.metadata["known_catalog"]["offer"]["available"] = False

    if change_point == "before_work":
        edit_offer()
        with pytest.raises(Cancelled):
            work()
        assert f.calls == []
    elif change_point == "during_get":
        def changed_holdings(*args, **kwargs):
            f.calls.append((args, kwargs))
            edit_offer()
            return f.detail
        f.service.holdings = changed_holdings
        success(work())
        assert len(f.calls) == 1
    else:
        assert work() is f.detail
        edit_offer()
        success(f.detail)

    assert f.outputs == []
    failure(RuntimeError("late response"))
    assert f.outputs == []


@pytest.mark.parametrize("attribute,value", [
    ("record_id", "other-record"),
    ("source_collection", "other-collection"),
    ("format", "pdf"),
    ("medium", "physical"),
])
def test_changed_loaded_notice_scalar_blocks_queued_get(monkeypatch, attribute, value):
    f = _fixture(monkeypatch)
    work, success, failure = _start(f)
    setattr(f.item, attribute, value)
    with pytest.raises(Cancelled):
        work()
    assert f.calls == [] and f.outputs == []
    success(f.detail)
    assert f.outputs == []


def test_unchanged_loaded_metadata_publishes_standard_actions(monkeypatch):
    f = _fixture(monkeypatch)
    f.item.metadata = {
        "known_catalog": {"edition_revision": "r1",
                          "offer": {"available": True}},
    }
    work, success, failure = _start(f)
    success(work())
    assert len(f.calls) == 1
    assert f.outputs == [((f.item, f.detail), {"context_sections": ()})]


def test_stale_loaded_notice_without_metadata_is_still_handled(monkeypatch):
    f = _fixture(monkeypatch)
    f.item.metadata = {}
    work, success, failure = _start(f)
    success(work())
    assert len(f.outputs) == 1
