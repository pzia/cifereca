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
             outcome: str | None = None) -> None:
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
                    + "\n"
                )
                self.stream.flush()
            except (OSError, ValueError):
                pass

    @contextmanager
    def phase(self, stage: str) -> Iterator[None]:
        if stage not in STAGES:
            raise ValueError("invalid_update_stage")
        started = self.clock()
        self.emit(stage, "ROLLBACK" if stage == "rollback" else "RUNNING")
        try:
            yield
        except BaseException:
            self.emit(stage, "ERROR", since=started)
            raise
        else:
            self.emit(stage, "OK", since=started)


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
    except BaseException:
        progress.emit("operation", "ERROR")
        progress.emit("operation", "DONE", outcome="failed")
        raise
    else:
        progress.emit("operation", "DONE", outcome="success")
    finally:
        _CURRENT.reset(token)


@contextmanager
def update_phase(stage: str) -> Iterator[None]:
    """Zero-I/O in API calls which did not opt in to progress reporting."""
    current = _CURRENT.get()
    if current is None:
        yield
    else:
        with current.phase(stage):
            yield


def step(stage: str, operation: Callable, *args, **kwargs):
    """Execute the exact original callable once; never replay on failure."""
    with update_phase(stage):
        return operation(*args, **kwargs)
