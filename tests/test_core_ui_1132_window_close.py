"""P0 #1132: test the real headless teardown coordinator."""
from types import SimpleNamespace as NS
from cifereca.gui.window_close import close_window_resources


class Config:
    def __init__(self, events, fail=False):
        self.events, self.fail = events, fail
    def set(self, key, value): self.events.append(("set", key, value))
    def save(self):
        self.events.append("save")
        if self.fail: raise OSError("fixture")


def _fixture(*, save_fails=False, runner_fails=False, lifetime_fails=()):
    events = []
    class Lifetime:
        def __init__(self, key): self.key = key
        def dispose(self):
            events.append("dispose:" + self.key)
            if self.key in lifetime_fails:
                raise RuntimeError("fixture dispose error")
    def closer(key, fail=False):
        def close():
            events.append(key)
            if fail: raise OSError("fixture")
        return NS(close=close)
    host = NS(
        _closed=False, _settings_main_lifetime=Lifetime("main"),
        _settings_standalone_lifetime=Lifetime("standalone"), _diag_refresh=object(),
        config=Config(events, save_fails), get_width=lambda:1366,
        get_height=lambda:900,
        _cancel_activity_hide=lambda:events.append("activity"),
        _cached_book_unsubscribe=lambda:events.append("unsubscribe"),
        runner=closer("runner", runner_fails),
        history_cover_resolver=closer("history"),
        covers=closer("covers"), service=closer("service"),
    )
    return host, events


def test_close_once_orders_existing_cleanup_and_destroys_settings_sessions():
    host, events = _fixture()
    timers = []
    assert close_window_resources(host, source_remove=timers.append) is False
    assert host._closed and host._diag_refresh is None
    assert host._settings_main_lifetime is None
    assert host._settings_standalone_lifetime is None
    assert timers == []
    assert events.index("dispose:main") < events.index("dispose:standalone")
    assert events.index("save") < events.index("activity") < events.index("unsubscribe")
    assert events[-4:] == ["runner","history","covers","service"]
    before = events[:]
    assert close_window_resources(host, source_remove=timers.append) is False
    assert events == before and timers == []


def test_close_failure_isolation_releases_all_resources():
    host, events = _fixture(save_fails=True, runner_fails=True)
    assert close_window_resources(host, source_remove=lambda _source: None) is False
    assert "unsubscribe" in events and "service" in events and "covers" in events


def test_missing_timer_never_removes_glib_source():
    host, events = _fixture()
    def forbidden(_source):
        raise AssertionError("no timer")
    close_window_resources(host, source_remove=forbidden)
    assert "runner" in events


def test_no_background_tasks_or_network_actions_during_close():
    host, events = _fixture()
    close_window_resources(host, source_remove=lambda _source: None)
    assert "service" in events and events.count("runner") == 1


def test_broken_settings_page_dispose_does_not_skip_remaining_teardown():
    for broken in (("main",), ("standalone",), ("main", "standalone")):
        host, events = _fixture(lifetime_fails=broken)
        timers = []
        assert close_window_resources(host, source_remove=timers.append) is False
        assert host._closed
        assert host._settings_main_lifetime is None
        assert host._settings_standalone_lifetime is None
        assert events.count("dispose:main") == 1
        assert events.count("dispose:standalone") == 1
        assert events.count("unsubscribe") == 1
        assert events[-4:] == ["runner", "history", "covers", "service"]
        assert timers == []
        assert close_window_resources(host, source_remove=timers.append) is False
        assert events.count("service") == 1 and timers == []


def test_close_revokes_all_page_leases_before_dispose_and_service_teardown():
    from cifereca.gui import page_lifecycle_controller
    from cifereca.plugin_api.page_lifecycle import PageLifecycleV1

    host, events = _fixture()
    dispatcher = page_lifecycle_controller.reset(host)
    dispatcher.context_changed("identity-a", None)
    leases = {}

    def context_changed(event):
        leases[event.route] = event

    def dispose(route, *, fail=False):
        events.append(("page_dispose", route, host._closed,
                       host._page_lifecycle_dispatcher is None,
                       tuple(lease.is_current(require_visible=False)
                             for lease in leases.values())))
        # A plugin can reenter the window close handler.
        close_window_resources(host, source_remove=lambda _source: None)
        if fail:
            raise RuntimeError("fixture page dispose error")

    for route in ("plugin:first", "plugin:second"):
        widget = NS(cifereca_page_lifecycle_v1=PageLifecycleV1(
            on_context_changed=context_changed,
            on_show=context_changed,
            on_dispose=lambda route=route: dispose(
                route, fail=(route == "plugin:first")),
        ))
        assert dispatcher.mount(route, widget)
    dispatcher.show("plugin:first")
    assert leases["plugin:first"].is_current()
    assert leases["plugin:second"].is_current(require_visible=False)

    assert close_window_resources(host, source_remove=lambda _source: None) is False
    assert events[:2] == [
        ("page_dispose", "plugin:first", True, True, (False, False)),
        ("page_dispose", "plugin:second", True, True, (False, False)),
    ]
    assert host._page_lifecycle_dispatcher is None
    assert events[-4:] == ["runner", "history", "covers", "service"]
    assert all(not lease.is_current(require_visible=False) for lease in leases.values())
    before = events[:]
    assert close_window_resources(host, source_remove=lambda _source: None) is False
    assert events == before
