"""P1 #4093: exact-process, bounded, raw session journal for GUI --capture.

Deliberately does NOT read cifereca.log, its rotations or the daily ZIP.
The canonical Core logger already formats raw events; this separate handler
keeps only records observed after start() and before finish(). It has no GTK,
provider, network, archive or persistent configuration dependency.

RAW: session output can contain credentials, signed URLs and PII. It must
only be written to a private explicitly requested local archive.
"""
from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import logging
import os
import threading

from ..logging_utils import CanonicalEventFormatter, RedactionFilter

_DEFAULT_LIMIT = 32 * 1024 * 1024
_MAX_LIMIT = 64 * 1024 * 1024
_MAX_RECORDS = 100_000
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class _CaptureHandler(logging.Handler):
    """Collect the same filtered canonical records, without past rotations."""

    def __init__(self, owner: "CaptureSessionJournal"):
        super().__init__(level=logging.DEBUG)
        self._owner = owner
        self.setFormatter(CanonicalEventFormatter(_LOG_FORMAT))
        self.addFilter(RedactionFilter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._owner._observe(record, self.format)
        except Exception:
            # Diagnostics must not terminate a genuine provider operation.
            with self._owner._mutex:
                self._owner._omitted += 1


class CaptureSessionJournal:
    """A process-local, bounded, once-only session journal.

    Attach before the first application log, then finish after the last
    shutdown log. Unread historical logs, other instances and later events
    are excluded by construction, not by timestamp filtering.

    Calling snapshot() exposes RAW unredacted log bytes to the local caller.
    """

    def __init__(self, *, max_bytes: int = _DEFAULT_LIMIT):
        if type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_LIMIT:
            raise ValueError("capture_journal_invalid_limit")
        self.max_bytes = max_bytes
        self.session_ref: str | None = None
        self.process_id: int | None = None
        self.started_at_utc: str | None = None
        self.finished_at_utc: str | None = None
        self._mutex = threading.RLock()
        self._state = "new"
        self._buffer = io.BytesIO()
        self._records = 0
        self._omitted = 0
        self._logger: logging.Logger | None = None
        self._handler = _CaptureHandler(self)

    def start(self, logger: logging.Logger) -> None:
        """Bind only to the canonical cifereca logger, never the root logger."""
        if not isinstance(logger, logging.Logger) or logger.name != "cifereca":
            raise ValueError("capture_journal_invalid_logger")
        with self._mutex:
            if self._state != "new":
                raise ValueError("capture_journal_already_started")
            import secrets
            self.session_ref = secrets.token_hex(16)
            self.process_id = os.getpid()
            self.started_at_utc = _utc_now()
            self._logger = logger
            self._state = "active"
            logger.addHandler(self._handler)
            # Old Numilog plugin versions use a non-propagating legacy logger.
            # Reuse the canonical handler, never parse the shared log file.
            from ..logging_utils import _share_canonical_handlers
            _share_canonical_handlers(logger)

    def _observe(self, record: logging.LogRecord, formatter) -> None:
        with self._mutex:
            if self._state != "active" or os.getpid() != self.process_id:
                return  # Forked workers cannot contaminate the GUI session.
            if record.process != self.process_id:
                return  # Also refuse forwarded records from another process.
            if self._records >= _MAX_RECORDS:
                self._omitted += 1
                return
            try:
                # Each JSONL line contains the unmodified canonical log
                # record as a string; multiline traceback stays in that one
                # field and cannot be confused with other sessions' lines.
                line = {
                    "timestamp_utc": datetime.fromtimestamp(
                        record.created, timezone.utc,
                    ).isoformat(timespec="milliseconds"),
                    "process_id": self.process_id,
                    # Carry the same opaque session key as manifest.json so
                    # individual JSONL records cannot be misattributed when
                    # multiple GUI captures share a PID or civil date.
                    "session_ref": self.session_ref,
                    "level": record.levelname,
                    "logger": record.name,
                    "raw": formatter(record),
                }
                raw = (json.dumps(
                    line, ensure_ascii=False, allow_nan=False,
                    separators=(",", ":"),
                ) + "\n").encode("utf-8")
            except (UnicodeError, ValueError, TypeError, RecursionError):
                self._omitted += 1
                return
            if self._buffer.tell() + len(raw) > self.max_bytes:
                self._omitted += 1
                return
            self._buffer.write(raw)
            self._records += 1

    def finish(self) -> None:
        """Close the logging window, including core shutdown records.

        Must be invoked after app.run returns and after any requested
        shutdown diagnostics. Idempotent, but start() cannot be repeated.
        """
        with self._mutex:
            if self._state == "new":
                raise ValueError("capture_journal_not_started")
            if self._state == "finished":
                return
            self._state = "finished"
            self.finished_at_utc = _utc_now()
            if self._logger is not None:
                self._logger.removeHandler(self._handler)
            logging.getLogger("cifereca_numilog").removeHandler(self._handler)
            self._handler.close()

    def snapshot(self) -> dict[str, object]:
        """Take only this instance's raw session records; never scan disk."""
        with self._mutex:
            if self._state != "finished":
                raise ValueError("capture_journal_not_finished")
            return {
                "schema_version": 1,
                "format": "cifereca-capture-session-journal",
                "session_ref": self.session_ref,
                "process_id": self.process_id,
                "started_at_utc": self.started_at_utc,
                "finished_at_utc": self.finished_at_utc,
                "coverage": "partial" if self._omitted else "complete",
                "omitted_records": self._omitted,
                "record_count": self._records,
                "raw": self._buffer.getvalue(),
            }
