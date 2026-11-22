"""#6397: queued public C work is inert after context revocation."""
from contextlib import closing
from types import SimpleNamespace as NS

import pytest

from cifereca.storage import Storage
from cifereca.gui import notice_c_web_action_controller as controller
from tests.test_core_p0_6397_web_click_completion import setup
from tests.test_core_p0_6397_known_search_web_actions import URL, GTK, request, deliver


@pytest.mark.parametrize("phase", ["menu", "click"])
@pytest.mark.parametrize("change", ["route", "service", "storage", "account", "source", "closed"])
def test_revoked_worker_never_reads_sqlite(tmp_path, monkeypatch, phase, change):
    with closing(Storage(tmp_path / "revoked.sqlite")) as db:
        c = setup(db)
        context = NS(mapped=True)
        pending, actions, launches, _, toasts = request(db, c, context=context)
        if phase == "click":
            deliver(pending)
            button, = actions
            button.callback(button)
        host = context.host
        core = host.service
        if change == "route":
            host._route_display_token = object()
        elif change == "service":
            host.service = NS(storage=db)
        elif change == "storage":
            core.storage = None
        elif change == "account":
            core._account_context_epoch = 1
        elif change == "source":
            core._source_context_epoch = 1
        else:
            host._closed = True

        def unexpected(*args, **kwargs):
            pytest.fail("revoked worker must not read SQLite")

        monkeypatch.setattr(controller, "read_public_web_choices", unexpected)
        monkeypatch.setattr(controller, "resolve_public_web_choice", unexpected)
        work, done, _ = pending.pop(0)
        assert work() is None
        done(None)
        assert len(actions) == int(phase == "click")
        assert not launches and not toasts


def test_worker_does_not_call_gtk_and_fresh_click_revalidates(tmp_path):
    with closing(Storage(tmp_path / "live.sqlite")) as db:
        c = setup(db)
        pending, actions, launches = [], [], []
        running = False

        def gtk_only():
            assert not running, "GTK callback called from worker"
            return True

        host = NS(service=NS(storage=db, _account_context_epoch=2, _source_context_epoch=3),
                  _route_display_token=object(), _closed=False,
                  runner=NS(submit=lambda label, work, done, fail, **kw:
                            pending.append((work, done, fail))),
                  toast=lambda text: pytest.fail(text))
        panel = NS(add_actions=actions.extend, get_mapped=gtk_only)
        gio = NS(AppInfo=NS(launch_default_for_uri=lambda uri, _: launches.append(uri)))

        def run_worker():
            nonlocal running
            work, done, _ = pending.pop(0)
            running = True
            try:
                result = work()
            finally:
                running = False
            done(result)

        assert controller.request_public_web_actions(
            host, panel, c.document, expected_epoch=c.epoch,
            current=gtk_only, Gtk=GTK, Gio=gio)
        run_worker()
        button, = actions
        button.callback(button)
        run_worker()
        assert launches == [URL]
        # A new deliberate click after returning to the same valid detail
        # may read the exact C again; an older queued click cannot do so.
        button.callback(button)
        host._route_display_token = object()
        run_worker()
        assert launches == [URL]
        # The old GTK button cannot silently adopt a newer route, even if
        # the caller's generic current() callback still returns True.
        button.callback(button)
        assert pending == []
        assert launches == [URL]
        # A new menu is a distinct explicit action on the new route.
        assert controller.request_public_web_actions(
            host, panel, c.document, expected_epoch=c.epoch,
            current=gtk_only, Gtk=GTK, Gio=gio)
        run_worker()
        actions[-1].callback(actions[-1])
        run_worker()
        assert launches == [URL, URL]
        assert db.entitlements() == [] and db.acquisitions() == []
