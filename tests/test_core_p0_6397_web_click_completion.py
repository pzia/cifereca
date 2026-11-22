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


def test_new_public_menu_request_revokes_old_queued_read_and_click(tmp_path):
    with closing(Storage(tmp_path / "menu-generation.sqlite")) as db:
        c = setup(db)
        pending, actions, launches = [], [], []
        host = NS(service=NS(storage=db),
                  runner=NS(submit=lambda label, work, done, fail, **kw:
                            pending.append((work, done, fail))),
                  toast=lambda _: None)
        panel = NS(add_actions=actions.extend)
        gio = NS(AppInfo=NS(launch_default_for_uri=lambda uri, _: launches.append(uri)))
        def ask():
            assert request_public_web_actions(
                host, panel, c.document, expected_epoch=c.epoch,
                current=lambda: True, Gtk=GTK, Gio=gio)
        ask()
        ask()
        work, done, _ = pending.pop(0)
        assert work() is None
        done(None)
        assert not actions
        deliver(pending)
        button, = actions
        button.callback(button)
        ask()
        work, done, _ = pending.pop(0)
        assert work() is None
        done(None)
        assert not launches
        button.callback(button)
        assert len(pending) == 1
        deliver(pending)
        actions[-1].callback(actions[-1])
        deliver(pending)
        assert launches == [URL]
