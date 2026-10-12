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


def _fixture(*, save_fails=False, runner_fails=False):
    events = []
    class Lifetime:
        def __init__(self, key): self.key = key
        def dispose(self): events.append("dispose:" + self.key)
    def closer(key, fail=False):
        def close():
            events.append(key)
            if fail: raise OSError("fixture")
        return NS(close=close)
    host = NS(
        _closed=False, _settings_main_lifetime=Lifetime("main"),
        _settings_standalone_lifetime=Lifetime("standalone"), _diag_refresh=object(),
        config=Config(events, save_fails), get_width=lambda:1366,
        get_height=lambda:900, _kobo_backup_timer_id=7,
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
    assert host._kobo_backup_timer_id is None and timers == [7]
    assert events.index("dispose:main") < events.index("dispose:standalone")
    assert events.index("save") < events.index("activity") < events.index("unsubscribe")
    assert events[-4:] == ["runner","history","covers","service"]
    before = events[:]
    assert close_window_resources(host, source_remove=timers.append) is False
    assert events == before and timers == [7]


def test_close_failure_isolation_releases_all_resources():
    host, events = _fixture(save_fails=True, runner_fails=True)
    assert close_window_resources(host, source_remove=lambda _source: None) is False
    assert "unsubscribe" in events and "service" in events and "covers" in events


def test_missing_timer_never_removes_glib_source():
    host, events = _fixture()
    host._kobo_backup_timer_id = None
    def forbidden(_source):
        raise AssertionError("no timer")
    close_window_resources(host, source_remove=forbidden)
    assert "runner" in events


def test_no_background_tasks_or_network_actions_during_close():
    host, events = _fixture()
    close_window_resources(host, source_remove=lambda _source: None)
    assert "service" in events and events.count("runner") == 1
