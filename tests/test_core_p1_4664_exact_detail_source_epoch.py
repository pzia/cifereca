"""#4664: Core source rebind revokes a queued or already completed detail.

The selection and loaded catalogue coordinates may remain identical while a
source is disconnected/reconnected. A lease must be bound to the Core epoch,
not just the visible GTK identity. No supplier HTTP is executed by fixtures.
"""

import pytest

from cifereca.gui import recommendation_exact_detail as port
from cifereca.plugin_api import Cancelled
from tests.test_core_p1_4664_recommendation_exact_detail import _fixture, _start


@pytest.mark.parametrize("known_source", [False, True], ids=["loaded", "known-v2"])
@pytest.mark.parametrize("changed", ["before_work", "before_delivery"])
def test_source_epoch_change_revokes_exact_actions(
    monkeypatch, known_source, changed,
):
    f = _fixture(monkeypatch)
    f.service._source_context_epoch = 7
    if known_source:
        monkeypatch.setattr(
            port, "_known_item",
            lambda window, coords: (f.item, {"revision": "v1"}),
        )
        f.kwargs["known_source"] = True
    work, success, failure = _start(f)

    if changed == "before_work":
        f.service._source_context_epoch = 8
        with pytest.raises(Cancelled):
            work()
        assert f.calls == []  # stale queued request never starts supplier GET
    else:
        assert work() is f.detail
        f.service._source_context_epoch = 8

    success(f.detail)
    failure(RuntimeError("late supplier reply"))
    assert f.outputs == []


@pytest.mark.parametrize("known_source", [False, True], ids=["loaded", "known-v2"])
def test_source_epoch_changed_during_holdings_cannot_publish_actions(
    monkeypatch, known_source,
):
    f = _fixture(monkeypatch)
    f.service._source_context_epoch = 10
    if known_source:
        monkeypatch.setattr(
            port, "_known_item",
            lambda window, coords: (f.item, {"revision": "v1"}),
        )
        f.kwargs["known_source"] = True
    work, success, failure = _start(f)

    def reconnect_during_read(*args, **kwargs):
        f.calls.append((args, kwargs))
        f.service._source_context_epoch = 11
        return f.detail

    f.service.holdings = reconnect_during_read
    success(work())
    assert len(f.calls) == 1
    assert f.outputs == []


def test_invalid_core_epoch_is_conservatively_refused(monkeypatch):
    f = _fixture(monkeypatch)
    f.service._source_context_epoch = -1
    assert not port.present_loaded_search_detail_in_place(
        f.window, **f.kwargs,
    )
    assert f.scheduled == [] and f.calls == []
