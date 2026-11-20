"""Exact current public web action consumes a single asynchronous completion."""
from contextlib import closing
from types import SimpleNamespace as NS

from cifereca.storage import Storage
from cifereca.gui.notice_c_web_action_controller import request_public_web_actions
from tests.test_core_p0_6397_c_web_reference_persistence import source, link, publish
from tests.test_core_p0_6397_known_search_web_actions import (
    URL, GTK, request, deliver,
)


def setup(db):
    item = source(db, "seller-a", "a", [link("seller-a", URL)])
    return publish(db, item.observation.key.edition_id, item)


def test_old_completion_cannot_consume_new_click_or_launch_twice(tmp_path):
    with closing(Storage(tmp_path / "duplicate.sqlite")) as db:
        c = setup(db)
        context = NS(mapped=True)
        pending, actions, launches, _, toasts = request(db, c, context=context)
        deliver(pending)
        button, = actions
        button.callback(button)
        work, done, fail = pending.pop(0)
        uri = work()
        done(uri)
        done(uri)
        assert launches == [URL]
        button.callback(button)
        assert len(pending) == 1
        done(uri)
        fail(RuntimeError("old callback"))
        button.callback(button)
        assert len(pending) == 1
        deliver(pending)
        assert launches == [URL, URL]
        assert not toasts


def test_submit_failure_leaves_button_reusable(tmp_path):
    with closing(Storage(tmp_path / "refused.sqlite")) as db:
        c = setup(db)
        context = NS(mapped=True)
        pending, actions, launches, _, toasts = request(db, c, context=context)
        deliver(pending)
        button, = actions
        runner = context.host.runner
        submit = runner.submit

        def refuse(*args, **kwargs):
            raise RuntimeError("queue unavailable")

        runner.submit = refuse
        button.callback(button)
        assert not pending and not launches
        assert len(toasts) == 1
        runner.submit = submit
        button.callback(button)
        deliver(pending)
        assert launches == [URL]


def test_initial_submit_failure_does_not_publish_actions(tmp_path):
    with closing(Storage(tmp_path / "initial.sqlite")) as db:
        c = setup(db)
        actions = []

        def refuse(*args, **kwargs):
            raise RuntimeError("queue unavailable")

        host = NS(service=NS(storage=db), runner=NS(submit=refuse))
        panel = NS(add_actions=actions.extend)
        assert not request_public_web_actions(
            host, panel, c.document, expected_epoch=c.epoch,
            current=lambda: True, Gtk=GTK, Gio=NS(),
        )
        assert actions == []
