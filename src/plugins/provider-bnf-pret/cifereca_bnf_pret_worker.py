"""TaskBroker-compatible, single-owner browser command loop (#2055).

The caller owns UI consent and marshals completion callbacks to its UI thread.
No browser exists before run(consent=True); stop() never touches Playwright
from a foreign thread. Private DTOs travel through command futures only, never
through TaskBroker results/progress or diagnostics.
"""
from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
from queue import Empty, Full, Queue
from threading import Event, RLock

from cifereca_bnf_pret_browser import BrowserSessionError, ReaderBrowserSession
from cifereca_bnf_pret_web import ProfileScope

_OPERATIONS = frozenset({"confirm_account", "loans", "suggestions", "detail", "visible_detail", "search", "dom_probe", "book_actions"})
_CODES = frozenset({
    "consent_required", "account_confirmation_required", "playwright_unavailable",
    "browser_unavailable", "login_not_verified", "auth_required", "access_refused",
    "rate_limited", "http_error", "session_closed", "scope_mismatch", "invalid_resource",
    "unrecognized_dom", "page_changed", "read_failed", "navigation_failed",
    "html_limit", "dom_limit", "item_limit", "invalid_session_state", "invalid_search",
})


def _safe_error(exc):
    code = str(exc) if isinstance(exc, BrowserSessionError) else "reader_operation_failed"
    return BrowserSessionError(code if code in _CODES else "reader_operation_failed")


@dataclass(frozen=True)
class _Command:
    operation: str
    scope: ProfileScope
    future: Future = field(repr=False)
    resource_id: str | None = field(default=None, repr=False)
    confirmed: bool = False
    query: str | None = field(default=None, repr=False)


class ReaderWorker:
    """Submit run() once to public tasks; explicitly stop on identity/UI teardown.

    scope is a local identity/account/epoch binding, not upstream authentication
    proof. The owner must invalidate the worker on a Core identity change and
    recheck the scope before displaying a completed private DTO.
    """

    def __init__(self, identity_id, account_id, *, session_factory=ReaderBrowserSession):
        # Validate identifiers without creating a browser or asserting an epoch.
        ProfileScope(identity_id, account_id, "pending")
        self._identity_id, self._account_id = identity_id, account_id
        self._factory = session_factory
        self._commands = Queue(maxsize=8)
        self._stop = Event()
        self._lock = RLock()
        self._state = "new"
        self._scope = None

    def __repr__(self):
        return f"ReaderWorker(state={self.state!r})"

    @property
    def state(self):
        with self._lock:
            return self._state

    @property
    def scope(self):
        with self._lock:
            return self._scope

    def stop(self):
        """Immediate invalidation; browser teardown remains on its owner thread."""
        with self._lock:
            self._stop.set()
            self._scope = None
            if self._state != "closed":
                self._state = "stopping"

    def submit(self, operation, *, expected, resource_id=None, account_confirmed=False, search_query=None):
        if type(operation) is not str or operation not in _OPERATIONS:
            raise BrowserSessionError("unsupported_reader_command")
        if operation == "confirm_account" and account_confirmed is not True:
            raise BrowserSessionError("account_confirmation_required")
        if operation == "detail" and (type(resource_id) is not str or not 1 <= len(resource_id) <= 128):
            raise BrowserSessionError("invalid_resource")
        if operation != "detail" and resource_id is not None:
            raise BrowserSessionError("invalid_resource")
        if operation == "search":
            if (type(search_query) is not str or not 2 <= len(search_query) <= 128
                    or any(ord(c) < 32 or ord(c) == 127 for c in search_query)
                    or not 2 <= len(" ".join(search_query.split())) <= 128):
                raise BrowserSessionError("invalid_search")
        elif search_query is not None:
            raise BrowserSessionError("invalid_search")
        with self._lock:
            if self._stop.is_set() or self._state not in {"login", "readonly"}:
                raise BrowserSessionError("session_closed")
            if not isinstance(expected, ProfileScope) or expected != self._scope:
                raise BrowserSessionError("scope_mismatch")
            future = Future()
            command = _Command(operation, expected, future, resource_id,
                               account_confirmed is True, search_query)
            try:
                self._commands.put_nowait(command)
            except Full:
                raise BrowserSessionError("reader_busy") from None
            return future

    def _cancelled(self, control):
        if self._stop.is_set():
            return True
        try:
            return bool(control.cancelled())
        except Exception:
            return True

    def _execute(self, session, command, control):
        if not command.future.set_running_or_notify_cancel():
            return
        with self._lock:
            valid = not self._cancelled(control) and command.scope == self._scope
        if not valid:
            command.future.set_exception(BrowserSessionError("session_closed"))
            return
        try:
            if command.operation == "confirm_account":
                value = session.finish_login(account_confirmed=command.confirmed)
            elif command.operation == "detail":
                value = session.detail(command.resource_id, expected=command.scope)
            elif command.operation == "visible_detail":
                value = session.current_detail(expected=command.scope)
            elif command.operation == "search":
                value = session.search(command.query, expected=command.scope)
            elif command.operation == "dom_probe":
                value = session.probe_visible_dom(expected=command.scope)
            elif command.operation == "book_actions":
                value = session.book_actions(expected=command.scope)
            elif command.operation == "loans":
                value = session.loans(expected=command.scope)
            else:
                value = session.suggestions(expected=command.scope)
            # stop/cancel invalidates a result even when it arrived successfully
            # from an in-flight browser read. Never publish it under a new scope.
            with self._lock:
                if self._cancelled(control) or command.scope != self._scope:
                    command.future.set_exception(BrowserSessionError("session_closed"))
                else:
                    if command.operation == "confirm_account":
                        self._state = "readonly"
                    command.future.set_result(value)
        except Exception as exc:
            command.future.set_exception(_safe_error(exc))

    def run(self, control, *, consent=False):
        """Runs on the TaskBroker worker; result is a closed, non-private code."""
        with self._lock:
            if self._state == "stopping" and self._stop.is_set():
                self._state = "closed"
                return "cancelled"
            if self._state != "new":
                raise BrowserSessionError("invalid_session_state")
            self._state = "starting"
        session = None
        try:
            if consent is not True:
                raise BrowserSessionError("consent_required")
            if self._cancelled(control):
                return "cancelled"
            session = self._factory(self._identity_id, self._account_id)
            session.open_login(consent=True)
            with self._lock:
                if self._cancelled(control):
                    return "cancelled"
                self._scope = session.scope
                self._state = "login"
            while not self._cancelled(control):
                try:
                    command = self._commands.get_nowait()
                except Empty:
                    session.pump()
                    continue
                self._execute(session, command, control)
                if not self._cancelled(control):
                    session.pump()
            return "cancelled"
        except Exception as exc:
            raise _safe_error(exc) from None
        finally:
            with self._lock:
                self._scope = None
                self._state = "closed"
                self._stop.set()
            # Resolve every outstanding command so UI callbacks aren't stranded.
            while True:
                try:
                    command = self._commands.get_nowait()
                except Empty:
                    break
                if command.future.set_running_or_notify_cancel():
                    command.future.set_exception(BrowserSessionError("session_closed"))
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
