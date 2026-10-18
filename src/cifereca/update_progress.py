"""Privacy-safe, real-time updater stages shared by CLI and future UI (#925).

Stages are closed vocabulary, never derived from an archive, repository, path,
subprocess output, plugin manifest, account or exception. Only CLI opts in to
stderr streaming; other callers may supply a structured event callback.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from time import monotonic
from typing import Callable, Iterator, TextIO
import secrets
import sys

STAGES = frozenset({
    "operation", "resolve_archive", "resolve_github", "clone", "fetch",
    "checkout", "metadata", "python_selection", "recover", "source_copy",
    "venv", "smoke", "backup", "plugin_snapshot", "transaction_prepare",
    "core_activation", "plugin_activation", "transaction_finalize",
    "cleanup", "rollback", "snapshot_copy", "snapshot_sha", "snapshot_extract",
    "manifest_validation", "plugin_backup", "plugin_install",
    "plugin_verification", "restart",
})
METHODS = frozenset({
    "github", "archive", "source", "rollback", "dev_snapshot",
    "dev_plugins_only",
})
STATES = frozenset({"START", "RUNNING", "OK", "ERROR", "SKIPPED", "ROLLBACK", "DONE"})
FAILURE_CODES = frozenset({"interrupted", "timeout", "update_error", "internal_error"})


def failure_code(exc: BaseException) -> str:
    """Classify by exception type only; never stringify arbitrary exceptions."""
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return "interrupted"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if type(exc).__name__ in {"UpdateError", "DevUpdateError"} and type(exc).__module__ in {
        "cifereca.updater", "cifereca.dev_updater",
    }:
        return "update_error"
    return "internal_error"

EventCallback = Callable[[dict[str, object]], None]
_CURRENT: ContextVar["UpdateProgress | None"] = ContextVar(
    "cifereca_update_progress", default=None,
)


class UpdateProgress:
    """One ephemeral operation; never store, render or log raw arguments."""

    def __init__(
        self, method: str, *,
        callback: EventCallback | None = None,
        stream: TextIO | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if method not in METHODS:
            raise ValueError("invalid_update_method")
        self.method = method
        self.transaction_ref = secrets.token_hex(6)
        self.callback = callback
        self.stream = stream
        self.clock = clock
        self.started = clock()

    def emit(self, stage: str, status: str, *, since: float | None = None,
             outcome: str | None = None, reason_code: str | None = None,
             plugin_index: int | None = None, plugin_total: int | None = None) -> None:
        if stage not in STAGES or status not in STATES:
            raise ValueError("invalid_update_event")
        now = self.clock()
        event: dict[str, object] = {
            "schema": "cifereca.update.progress/1",
            "transaction_ref": self.transaction_ref,
            "method": self.method,
            "stage": stage,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "elapsed_ms": min(86_400_000, max(0, int((now - self.started) * 1000))),
            "duration_ms": (
                min(86_400_000, max(0, int((now - since) * 1000)))
                if since is not None else 0
            ),
        }
        if status == "ERROR" and reason_code in FAILURE_CODES:
            event["reason_code"] = reason_code
        # The ordinal is a *position*, not an installed count or a guessed
        # percent. Accept only bounded exact ints, never plugin names/paths.
        if (stage == "plugin_install"
                and type(plugin_index) is int and type(plugin_total) is int
                and 1 <= plugin_index <= plugin_total <= 2048):
            event["plugin_index"] = plugin_index
            event["plugin_total"] = plugin_total
        if outcome in {"success", "failed"} and status == "DONE":
            event["outcome"] = outcome
        # Observability must not change the installation's outcome. A GUI
        # listener may detach while a subprocess is still running.
        if self.callback is not None:
            try:
                self.callback(dict(event))
            except Exception:
                pass
        if self.stream is not None:
            try:
                self.stream.write(
                    f"{event['timestamp']} update[{self.transaction_ref}] "
                    f"{self.method}/{stage} {status} "
                    f"elapsed={event['elapsed_ms']}ms "
                    f"duration={event['duration_ms']}ms"
                    + (f" outcome={event['outcome']}" if "outcome" in event else "")
                    + (f" reason={event['reason_code']}" if "reason_code" in event else "")
                    + (f" plugin={event['plugin_index']}/{event['plugin_total']}"
                       if "plugin_index" in event else "")
                    + "\n"
                )
                self.stream.flush()
            except (OSError, ValueError):
                pass

    @contextmanager
    def phase(
        self, stage: str, *, plugin_index: int | None = None,
        plugin_total: int | None = None,
    ) -> Iterator[None]:
        if stage not in STAGES:
            raise ValueError("invalid_update_stage")
        started = self.clock()
        ordinal = {"plugin_index": plugin_index, "plugin_total": plugin_total}
        self.emit(stage, "ROLLBACK" if stage == "rollback" else "RUNNING", **ordinal)
        try:
            yield
        except BaseException as exc:
            self.emit(stage, "ERROR", since=started, reason_code=failure_code(exc), **ordinal)
            raise
        else:
            self.emit(stage, "OK", since=started, **ordinal)


@contextmanager
def update_operation(
    method: str, *, callback: EventCallback | None = None,
    stream: TextIO | None = None,
) -> Iterator[UpdateProgress]:
    """Opt-in scoped instrumentation, compatible with a future UI callback.

    ContextVar keeps nested updater calls on one opaque operation ref and
    allows concurrent callers without a module-global mutable transaction.
    """
    progress = UpdateProgress(method, callback=callback, stream=stream)
    token = _CURRENT.set(progress)
    progress.emit("operation", "START")
    try:
        yield progress
    except BaseException as exc:
        progress.emit("operation", "ERROR", reason_code=failure_code(exc))
        progress.emit("operation", "DONE", outcome="failed")
        raise
    else:
        progress.emit("operation", "DONE", outcome="success")
    finally:
        _CURRENT.reset(token)


@contextmanager
def update_phase(
    stage: str, *, plugin_index: int | None = None,
    plugin_total: int | None = None,
) -> Iterator[None]:
    """Zero-I/O in API calls which did not opt in to progress reporting."""
    current = _CURRENT.get()
    if current is None:
        yield
    else:
        with current.phase(
            stage, plugin_index=plugin_index, plugin_total=plugin_total,
        ):
            yield


def step(stage: str, operation: Callable, *args, **kwargs):
    """Execute the exact original callable once; never replay on failure."""
    with update_phase(stage):
        return operation(*args, **kwargs)
