"""A public C menu request publishes at most one initial completion."""
from contextlib import closing
from types import SimpleNamespace as NS

import pytest

from cifereca.storage import Storage
from cifereca.gui.notice_c_web_action_controller import request_public_web_actions
from tests.test_core_p0_6397_web_click_completion import setup
from tests.test_core_p0_6397_known_search_web_actions import URL, GTK, request, deliver


@pytest.mark.parametrize("first", ["ok", "failure", "empty", "stale"])
def test_initial_delivery_can_be_consumed_only_once(tmp_path, first):
    with closing(Storage(tmp_path / "menu.sqlite")) as db:
        c = setup(db)
        pending, actions, launches, generation, toasts = request(db, c)
        work, done, fail = pending.pop(0)
        menu = work()
        if first == "ok":
            done(menu)
        elif first == "failure":
            fail(RuntimeError("worker failed"))
        elif first == "empty":
            done(None)
        else:
            generation[0] = 1
            done(menu)
            generation[0] = 0
        done(menu)
        fail(RuntimeError("duplicate"))
        done(menu)
        assert len(actions) == int(first == "ok")
        assert not pending and not launches and not toasts
        if actions:
            button, = actions
            button.callback(button)
            deliver(pending)
            assert launches == [URL]


def test_late_completion_after_submit_exception_is_inert(tmp_path):
    with closing(Storage(tmp_path / "refused-menu.sqlite")) as db:
        c = setup(db)
        callbacks, actions = [], []

        def refuse(_label, work, done, fail, **kwargs):
            callbacks.append((work, done, fail))
            raise RuntimeError("submission refused")

        host = NS(service=NS(storage=db), runner=NS(submit=refuse))
        panel = NS(add_actions=actions.extend)
        assert not request_public_web_actions(
            host, panel, c.document, expected_epoch=c.epoch,
            current=lambda: True, Gtk=GTK, Gio=NS(),
        )
        work, done, fail = callbacks[0]
        done(work())
        fail(RuntimeError("late failure"))
        assert not actions
