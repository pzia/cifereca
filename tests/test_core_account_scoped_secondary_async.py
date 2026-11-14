from __future__ import annotations

import ast
from pathlib import Path
from threading import Lock
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account, Booking, HistoryItem
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService
from cifereca.core import library_projection
from cifereca.gui import shell_account_selection_controller


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _accounts():
    first = Account(unique_id="a", provider_id="paris", credential_id="vault-a",
                    identity_id="alice")
    second = Account(unique_id="b", provider_id="paris", credential_id="vault-b",
                     identity_id="alice")
    return first, second


def _ui_host():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    method_names = {
        "_identity_request_current", "refresh_history",
        "_account_changed",
    }
    methods = [
        n for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name in method_names
    ]
    assert len(methods) == len(method_names)
    module = ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            *methods,
        ],
        type_ignores=[],
    )
    namespace = {"TaskPriority": NS(USER_ACTION="user", NORMAL="normal"),
                 "shell_account_selection_controller": shell_account_selection_controller,
                 "library_projection": library_projection}
    exec(compile(ast.fix_missing_locations(module), str(MAIN), "exec"), namespace)

    class Runner:
        def __init__(self):
            self.pending = []

        def submit(self, _title, work, done, **kwargs):
            self.pending.append(NS(work=work, done=done, options=kwargs))

    first, second = _accounts()
    service = NS(
        current_identity=NS(identity_id="alice"),
        current_account=first,
        last_history=[],
        last_bookings=[],
        refresh_history=lambda **_kw: ["history"],
    )
    accounts = (first, second)

    def select_account(unique_id, _provider, _credential):
        service.current_account = next(a for a in accounts if a.unique_id == unique_id)

    service.select_account = select_account
    host = type("AccountHost", (), {n: namespace[n] for n in method_names})()
    host.service = service
    host.runner = Runner()
    host._identity_context_generation = 1
    host._account_context_generation = 1
    host._account_changing = False
    host._visible_accounts = list(accounts)
    host.history_objects = {"old": "history"}
    host.booking_objects = {"old": "booking"}
    host._history_all_rows = ["old"]
    host._history_rows = ["old"]
    host._history_loaded_count = 3
    host._history_append_scheduled = True
    host.history_displayed = []
    host._populate_history = lambda rows: host.history_displayed.append(rows)
    host.history_page = NS(show_rows=lambda rows: None, detail=NS(empty=lambda *_a: None))
    host.booking_page = NS(show_rows=lambda rows: None, detail=NS(empty=lambda *_a: None))
    host.toast = lambda *_args: None
    return host


@pytest.mark.parametrize("method,displayed", [
    ("refresh_history", "history_displayed"),
])
def test_switching_account_discards_inflight_secondary_page_callback(method, displayed):
    host = _ui_host()
    getattr(host, method)()
    old = host.runner.pending[-1]
    host._account_changed(NS(get_selected=lambda: 1), None)
    assert host.service.current_account.unique_id == "b"
    old.done(["a private row"])
    assert getattr(host, displayed) == []

    getattr(host, method)()
    host.runner.pending[-1].done(["b visible row"])
    assert getattr(host, displayed) == [["b visible row"]]


@pytest.mark.parametrize("method,displayed", [
    ("refresh_history", "history_displayed"),
])
def test_account_a_b_a_disallows_callback_from_older_generation(method, displayed):
    host = _ui_host()
    getattr(host, method)()
    old = host.runner.pending[-1]
    host._account_changed(NS(get_selected=lambda: 1), None)
    host._account_changed(NS(get_selected=lambda: 0), None)
    assert host.service.current_account.unique_id == "a"
    old.done(["a obsolete"])
    assert getattr(host, displayed) == []
    getattr(host, method)()
    host.runner.pending[-1].done(["a fresh"])
    assert getattr(host, displayed) == [["a fresh"]]


def test_account_switch_clears_secondary_page_models_even_with_same_identity():
    host = _ui_host()
    host._account_changed(NS(get_selected=lambda: 1), None)
    assert host.history_objects == {}
    assert host.booking_objects == {}
    assert host._history_all_rows == []
    assert host._history_rows == []
    assert host._history_loaded_count == 0
    assert host._history_append_scheduled is False
    assert host.service.last_history == []
    assert host.service.last_bookings == []
    assert host._identity_context_generation == 1
    assert host._account_context_generation == 2


def _service_host():
    host = LibraryService.__new__(LibraryService)
    first, second = _accounts()
    host.accounts = [first, second]
    host.current_account = first
    host.current_identity = NS(identity_id="alice")
    host._account_context_epoch = 1
    # Production services create this lock in __init__; __new__ bypasses it.
    host._search_generation_lock = Lock()
    host.last_loans = []
    host._sessions = {}
    host.identity_by_id = lambda _id: None
    host.last_history = ["existing active history"]
    host.last_bookings = ["existing active booking"]
    host.config = NS(get=lambda _key, default: default)
    writes = []
    host.storage = NS(
        cache_get=lambda *_a: None,
        cache_put=lambda kind, key, value, ttl: writes.append((kind, key, value, ttl)),
    )
    host.diagnostics = NS(add=lambda *_args, **_kw: None)
    return host, writes


def test_service_history_fetch_uses_original_account_and_cannot_repopulate_new_account():
    host, writes = _service_host()
    calls = []
    row = HistoryItem(id="history-a", provider="paris", medium="digital",
                      record_id="book-1", title="Private A")

    def fetch(account):
        calls.append(account.unique_id)
        host.select_account("b", "paris", "vault-b")
        return [row]

    host._session_for_account = lambda account: NS(
        capabilities=NS(history=True), history=fetch,
    )
    result = host.refresh_history(force=True)
    assert host.last_history == ["existing active history"]
    assert result == [row]
    assert calls == ["a"]
    assert host.current_account.unique_id == "b"
    assert len(writes) == 1
    assert writes[0][1] == library_projection._history_cache_key(host.accounts[0])
    assert writes[0][1] != library_projection._history_cache_key(host.accounts[1])


def test_service_history_never_restores_a_b_a_stale_snapshot():
    host, _writes = _service_host()
    row = HistoryItem(id="stale", provider="paris", medium="digital",
                      record_id="book", title="Stale history")

    def fetch(_account):
        host.select_account("b", "paris", "vault-b")
        host.select_account("a", "paris", "vault-a")
        return [row]

    host._session_for_account = lambda account: NS(
        capabilities=NS(history=True), history=fetch,
    )
    assert host.refresh_history(force=True) == [row]
    assert host.last_history == ["existing active history"]
    assert host.current_account.unique_id == "a"
    assert host._account_context_epoch == 3


@pytest.mark.parametrize("force", [False, True])
def test_retired_bookings_never_read_cache_or_contact_old_installed_plugin(force):
    host, writes = _service_host()
    old = host.current_account

    def forbidden(*_args, **_kwargs):
        raise AssertionError("retired reservation path contacted a provider or cache")

    # A v1 plugin can still claim bookings=True; Core must default-deny
    # regardless of capability, cached rows and refresh/force arguments.
    host._session_for_account = forbidden
    host.storage.cache_get = forbidden
    host.storage.cache_put = forbidden
    host._key = forbidden

    assert host.refresh_bookings(force=force) == []
    assert host.last_bookings == []
    assert host.current_account is old
    assert host._account_context_epoch == 1
    assert writes == []


def test_retired_booking_read_never_projects_historical_cache_as_rights():
    host, _writes = _service_host()
    host.last_bookings = [Booking(id="legacy", provider="paris",
                                  medium="digital", title="Historical booking")]
    assert host.refresh_bookings() == []
    assert host.last_bookings == []


def test_queued_history_worker_uses_admitted_account_after_ui_switch(monkeypatch):
    host = _ui_host()
    admitted = host.service.current_account
    calls = []

    def read(_service, *, force=False, account=None):
        calls.append((force, account))
        return ["history for A"]

    monkeypatch.setattr(library_projection, "refresh_history", read)
    host.refresh_history(force=True)
    pending = host.runner.pending[-1]
    host._account_changed(NS(get_selected=lambda: 1), None)
    assert host.service.current_account.unique_id == "b"
    assert pending.work() == ["history for A"]
    assert calls == [(True, admitted)]
    pending.done(["history for A"])
    assert host.history_displayed == []


def test_history_explicit_account_uses_its_cache_and_session_after_switch():
    host, writes = _service_host()
    admitted = host.current_account
    host.select_account("b", "paris", "vault-b")
    seen = []
    host._session_for_account = lambda account: (
        seen.append(account) or NS(
            capabilities=NS(history=True),
            history=lambda account: [],
        )
    )
    host._key = lambda *_args: (_ for _ in ()).throw(
        AssertionError("mutable navigation cache key")
    )
    assert library_projection.refresh_history(host, force=True, account=admitted) == []
    assert seen == [admitted]
    assert len(writes) == 1
    assert writes[0][1] == library_projection._history_cache_key(admitted)
    assert writes[0][1].startswith("history-v4:")
    assert host.current_account.unique_id == "b"


def test_history_worker_requires_admitted_account_before_any_io():
    host, writes = _service_host()
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("io")
        raise AssertionError("unscoped history touched cache or provider")

    host.storage.cache_get = forbidden
    host.storage.cache_put = forbidden
    host._session_for_account = forbidden
    host.config.get = forbidden
    with pytest.raises(TypeError):
        library_projection.refresh_history(host)
    for invalid in (
        None, NS(identity_id="alice"),
        Account(unique_id="shared", identity_id=""),
        Account(unique_id="shared", provider_id=""),
        Account(unique_id="shared", credential_id=""),
        Account(unique_id=None),
    ):
        with pytest.raises(ProviderConfigurationError):
            library_projection.refresh_history(host, account=invalid)
    assert calls == []
    assert writes == []


def test_history_cache_never_reuses_old_or_other_identity_borrower_rows():
    host, _writes = _service_host()
    from dataclasses import asdict

    alice = Account(identity_id="alice", provider_id="synthetic",
                    credential_id="same-credential", unique_id="same-account")
    bob = Account(identity_id="bob", provider_id="synthetic",
                  credential_id="same-credential", unique_id="same-account")
    legacy = HistoryItem(id="old-private", provider="synthetic", medium="digital",
                         record_id="old", title="Legacy private cache")
    cache = {"synthetic:same-credential:same-account:history-v3": [asdict(legacy)]}
    reads, calls = [], []

    def cache_get(kind, key):
        assert kind == "account"
        reads.append(key)
        return cache.get(key)

    def cache_put(kind, key, value, _ttl):
        assert kind == "account"
        cache[key] = value

    def session_for_account(account):
        calls.append(account.identity_id)
        return NS(
            capabilities=NS(history=True),
            history=lambda _account: [HistoryItem(
                id=account.identity_id, provider="synthetic", medium="digital",
                record_id=account.identity_id, title=account.identity_id,
                identity_id=account.identity_id,
            )],
        )

    host.storage.cache_get = cache_get
    host.storage.cache_put = cache_put
    host._session_for_account = session_for_account
    assert [r.id for r in library_projection.refresh_history(host, account=alice)] == ["alice"]
    assert [r.id for r in library_projection.refresh_history(host, account=bob)] == ["bob"]
    assert [r.id for r in library_projection.refresh_history(host, account=alice)] == ["alice"]
    assert calls == ["alice", "bob"]
    assert all(key.startswith("history-v4:") for key in reads)
    assert len(set(reads)) == 2
    assert cache["synthetic:same-credential:same-account:history-v3"] == [asdict(legacy)]


def test_history_cache_key_does_not_alias_colon_delimited_account_coordinates():
    first = Account(identity_id="owner", provider_id="synthetic:x",
                    credential_id="credential", unique_id="borrower")
    second = Account(identity_id="owner", provider_id="synthetic",
                     credential_id="x:credential", unique_id="borrower")
    assert library_projection._history_cache_key(first) != library_projection._history_cache_key(second)
