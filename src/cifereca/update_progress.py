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
import json
import sys
import re

STAGES = frozenset({
    "operation", "resolve_archive", "resolve_github", "clone", "fetch",
    "checkout", "metadata", "python_selection", "recover", "source_copy",
    "venv", "smoke", "backup", "plugin_snapshot", "transaction_prepare",
    "core_activation", "plugin_activation", "transaction_finalize",
    "cleanup", "rollback", "snapshot_copy", "snapshot_sha", "snapshot_extract",
    "manifest_validation", "plugin_backup", "plugin_install",
    "plugin_verification", "restart", "plugin_inspect", "plugin_unpack",
    "plugin_dependencies", "plugin_enable",
})
METHODS = frozenset({
    "github", "archive", "source", "rollback", "dev_snapshot",
    "dev_plugins_only",
})
STATES = frozenset({"START", "RUNNING", "OK", "ERROR", "SKIPPED", "ROLLBACK", "DONE"})
SMOKE_FAILURE_CODES = frozenset({
    "smoke_cli_failed", "smoke_json_invalid", "smoke_inventory_invalid",
    "smoke_contributions_failed", "smoke_active_missing",
})
SMOKE_COUNT_FIELDS = frozenset({
    "contributions_error", "contributions_incompatible", "active_packages_missing",
})
FAILURE_CODES = frozenset({"interrupted", "timeout", "update_error", "internal_error"}) | SMOKE_FAILURE_CODES


def smoke_evidence(exc: BaseException) -> dict[str, int] | None:
    """Trust only local updater exceptions and exact bounded smoke counters."""
    if (type(exc).__name__, type(exc).__module__) not in {
        ("PluginSmokeError", "cifereca.dev_updater"),
        ("ReleasePluginSmokeError", "cifereca.updater"),
    }:
        return None
    values = getattr(exc, "smoke_counts", None)
    if (type(values) is not dict or set(values) != SMOKE_COUNT_FIELDS
            or any(type(v) is not int or not 0 <= v <= 2048 for v in values.values())):
        return None
    return dict(values)


def failure_code(exc: BaseException) -> str:
    """Classify by exception type only; never stringify arbitrary exceptions."""
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return "interrupted"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if smoke_evidence(exc) is not None:
        code = getattr(exc, "reason_code", None)
        if type(code) is str and code in SMOKE_FAILURE_CODES:
            return code
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
        self._stage_stack: list[tuple[str, float, dict[str, object]]] = []
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
                    stage, started, metadata = self._stage_stack[-1]
                    # Periodic RUNNING means only "still in stage": never
                    # claim a measured percent, a complete subprocess or
                    # a count of successfully installed plugins.
                    self.emit(stage, "RUNNING", since=started, **metadata)

    def emit(self, stage: str, status: str, *, since: float | None = None,
             outcome: str | None = None, reason_code: str | None = None,
             plugin_index: int | None = None, plugin_total: int | None = None,
             package_id: str | None = None, package_version: str | None = None,
             smoke_counts: dict[str, int] | None = None) -> None:
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
        if (stage in {"smoke", "plugin_activation"} and status == "ERROR"
                and reason_code in SMOKE_FAILURE_CODES
                and type(smoke_counts) is dict
                and set(smoke_counts) == SMOKE_COUNT_FIELDS
                and all(type(v) is int and 0 <= v <= 2048 for v in smoke_counts.values())):
            event["smoke_counts"] = dict(smoke_counts)
        # The ordinal is a *position*, not an installed count or a guessed
        # percent. Accept only bounded exact ints, never plugin names/paths.
        if (stage == "plugin_install"
                and type(plugin_index) is int and type(plugin_total) is int
                and 1 <= plugin_index <= plugin_total <= 2048):
            event["plugin_index"] = plugin_index
            event["plugin_total"] = plugin_total
            # Only identities read from validated release ZIP manifests are
            # supplied by the dev updater, never from CLI stderr.
            if (type(package_id) is str and len(package_id) <= 128
                    and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", package_id)):
                event["package_id"] = package_id
                if (type(package_version) is str and len(package_version) <= 64
                        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]*", package_version)):
                    event["package_version"] = package_version
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
                    + (f" package={event['package_id']}@{event['package_version']}"
                       if "package_version" in event else "")
                    + ((" smoke_errors=" + str(event["smoke_counts"]["contributions_error"])
                        + " smoke_incompatible=" + str(event["smoke_counts"]["contributions_incompatible"])
                        + " smoke_missing=" + str(event["smoke_counts"]["active_packages_missing"]))
                       if "smoke_counts" in event else "")
                    + "\n"
                )
                self.stream.flush()
            except (OSError, ValueError):
                pass

    @contextmanager
    def phase(
        self, stage: str, *, plugin_index: int | None = None,
        plugin_total: int | None = None, package_id: str | None = None,
        package_version: str | None = None,
    ) -> Iterator[None]:
        if stage not in STAGES:
            raise ValueError("invalid_update_stage")
        started = self.clock()
        ordinal = {"plugin_index": plugin_index, "plugin_total": plugin_total,
                   "package_id": package_id, "package_version": package_version}
        with self._stage_lock:
            self._stage_stack.append((stage, started, ordinal))
            self.emit(stage, "ROLLBACK" if stage == "rollback" else "RUNNING", **ordinal)
        try:
            yield
        except BaseException as exc:
            with self._stage_lock:
                self._stage_stack.pop()
                self.emit(stage, "ERROR", since=started, reason_code=failure_code(exc),
                          smoke_counts=smoke_evidence(exc), **ordinal)
            raise
        else:
            with self._stage_lock:
                self._stage_stack.pop()
                self.emit(stage, "OK", since=started, **ordinal)


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
    except BaseException as exc:
        progress.stop_heartbeat()
        progress.emit("operation", "ERROR", reason_code=failure_code(exc))
        progress.emit("operation", "DONE", outcome="failed")
        raise
    else:
        progress.stop_heartbeat()
        progress.emit("operation", "DONE", outcome="success")
    finally:
        progress.stop_heartbeat()
        _CURRENT.reset(token)


@contextmanager
def update_phase(
    stage: str, *, plugin_index: int | None = None,
    plugin_total: int | None = None, package_id: str | None = None,
    package_version: str | None = None,
) -> Iterator[None]:
    """Zero-I/O in API calls which did not opt in to progress reporting."""
    current = _CURRENT.get()
    if current is None:
        yield
    else:
        with current.phase(
            stage, plugin_index=plugin_index, plugin_total=plugin_total,
            package_id=package_id, package_version=package_version,
        ):
            yield


def step(stage: str, operation: Callable, *args, **kwargs):
    """Execute the exact original callable once; never replay on failure."""
    with update_phase(stage):
        return operation(*args, **kwargs)


def skip_update_phase(stage: str) -> None:
    """Record a deliberately *unexecuted* stage of an opted-in operation.

    Unlike update_phase/step, this performs no work and never reports OK.
    """
    progress = _CURRENT.get()
    if progress is not None:
        progress.emit(stage, "SKIPPED")


def jsonl_progress_writer(stream: TextIO) -> EventCallback:
    """Internal child capability: only collector-built events, one flush each."""
    def write(event: dict[str, object]) -> None:
        stream.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")
        stream.flush()
    return write


def child_progress_receiver() -> Callable[[bytes], None]:
    """Capture the parent context; never trust child metadata or raw logs.

    Child operation completion cannot complete the parent's transaction. Only
    closed phase/status fields and bounded factual ordinals cross the boundary.
    """
    progress = _CURRENT.get()
    def receive(line: bytes) -> None:
        if progress is None:
            return
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            return
        if type(value) is not dict or value.get("schema") != "cifereca.update.progress/1":
            return
        stage, status = value.get("stage"), value.get("status")
        if (type(stage) is not str or stage not in STAGES or stage == "operation"
                or type(status) is not str
                or status not in {"RUNNING", "OK", "ERROR", "SKIPPED", "ROLLBACK"}):
            return
        reason = value.get("reason_code")
        if type(reason) is not str or reason not in FAILURE_CODES:
            reason = None
        duration = value.get("duration_ms")
        since = (progress.clock() - duration / 1000
                 if type(duration) is int and 0 <= duration <= 86_400_000 else None)
        counts = value.get("smoke_counts")
        if (type(counts) is not dict or set(counts) != SMOKE_COUNT_FIELDS
                or any(type(v) is not int or not 0 <= v <= 2048
                       for v in counts.values())):
            counts = None
        with progress._stage_lock:
            progress.emit(stage, status, since=since, reason_code=reason,
                          smoke_counts=counts,
                          plugin_index=value.get("plugin_index"),
                          plugin_total=value.get("plugin_total"))
    return receive
