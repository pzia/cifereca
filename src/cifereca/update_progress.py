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
from threading import Event, RLock, Thread
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
        heartbeat_seconds: float = 10.0,
    ) -> None:
        if method not in METHODS:
            raise ValueError("invalid_update_method")
        self.method = method
        self.transaction_ref = secrets.token_hex(6)
        self.callback = callback
        self.stream = stream
        self.clock = clock
        self.started = clock()
        # Only one ticker per opted-in installation; phases themselves run on
        # the caller thread. Nested phases keep the innermost active stage.
        self.heartbeat_seconds = max(0.005, float(heartbeat_seconds))
        self._stage_lock = RLock()
        self._stage_stack: list[tuple[str, float]] = []
        self._stop_heartbeat = Event()
        self._heartbeat: Thread | None = None

    def start_heartbeat(self) -> None:
        if self.callback is None and self.stream is None:
            return
        self._heartbeat = Thread(
            target=self._heartbeat_loop,
            name="cifereca-update-progress",
            daemon=True,
        )
        self._heartbeat.start()

    def stop_heartbeat(self) -> None:
        self._stop_heartbeat.set()
        if self._heartbeat is not None:
            # Event.wait() stops immediately when signaled. Never let
            # observability block a failed rollback or a CLI interruption.
            self._heartbeat.join(timeout=0.25)

    def _heartbeat_loop(self) -> None:
        while not self._stop_heartbeat.wait(self.heartbeat_seconds):
            with self._stage_lock:
                if self._stage_stack and not self._stop_heartbeat.is_set():
                    stage, started = self._stage_stack[-1]
                    # Periodic RUNNING means only "still in stage": never
                    # claim a measured percent, a complete subprocess or
                    # a count of successfully installed plugins.
                    self.emit(stage, "RUNNING", since=started)

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
        with self._stage_lock:
            self._stage_stack.append((stage, started))
            self.emit(stage, "ROLLBACK" if stage == "rollback" else "RUNNING")
        try:
            yield
        except BaseException:
            with self._stage_lock:
                self._stage_stack.pop()
                self.emit(stage, "ERROR", since=started)
            raise
        else:
            with self._stage_lock:
                self._stage_stack.pop()
                self.emit(stage, "OK", since=started)


@contextmanager
def update_operation(
    method: str, *, callback: EventCallback | None = None,
    stream: TextIO | None = None,
    heartbeat_seconds: float = 10.0,
) -> Iterator[UpdateProgress]:
    """Opt-in scoped instrumentation, compatible with a future UI callback.

    ContextVar keeps nested updater calls on one opaque operation ref and
    allows concurrent callers without a module-global mutable transaction.
    """
    progress = UpdateProgress(
        method, callback=callback, stream=stream,
        heartbeat_seconds=heartbeat_seconds,
    )
    token = _CURRENT.set(progress)
    progress.emit("operation", "START")
    progress.start_heartbeat()
    try:
        yield progress
    except BaseException:
        progress.stop_heartbeat()
        progress.emit("operation", "ERROR")
        progress.emit("operation", "DONE", outcome="failed")
        raise
    else:
        progress.stop_heartbeat()
        progress.emit("operation", "DONE", outcome="success")
    finally:
        progress.stop_heartbeat()
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
