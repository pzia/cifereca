"""#3649: explicit, local ZIP export of today's canonical raw journal events.

This is a diagnostics-only reader. It never opens provider sessions, changes
the journal, pseudonymizes evidence or creates an HTTP request.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
import os
import re
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

from .diagnostic_journal import _events

_MAX_EVENTS = 50_000
_MAX_JSON_BYTES = 48_000_000
_ZONE_NAME = re.compile(r"[A-Za-z0-9_.+\-]+(?:/[A-Za-z0-9_.+\-]+)*\Z")


def _zone(zone_name: str | None) -> tuple[ZoneInfo, str]:
    """Resolve the actual IANA zone, not a fixed UTC offset (DST boundary)."""
    candidates = [zone_name] if zone_name is not None else [os.environ.get("TZ")]
    if zone_name is None:
        try:
            candidates.append(Path("/etc/timezone").read_text(encoding="ascii")[:128].strip())
        except (OSError, UnicodeError):
            pass
        try:
            localtime = Path("/etc/localtime").resolve(strict=True)
            marker = "/zoneinfo/"
            if marker in str(localtime):
                candidates.append(str(localtime).split(marker, 1)[1])
        except (OSError, RuntimeError):
            pass
    for candidate in candidates:
        if (type(candidate) is not str or not 1 <= len(candidate) <= 128
                or _ZONE_NAME.fullmatch(candidate) is None
                or ".." in candidate):
            continue
        try:
            return ZoneInfo(candidate), candidate
        except (ZoneInfoNotFoundError, ValueError, OSError):
            continue
    # A fixed-offset local tzinfo cannot express the spring/autumn midnight
    # transition. Refuse rather than export a falsely labelled "today" ZIP.
    raise ValueError("journal_timezone_unavailable")


def export_daily_journal_zip(
    log_path: Path, destination: str | Path, *, now: datetime | None = None,
    zone_name: str | None = None, cancelled=None,
) -> Path:
    """Publish a complete ZIP atomically, without overwriting another export.

    Reads the one canonical rotating journal. Every retained event within the
    exact local-day half-open UTC bounds is included regardless of UI filters,
    severity or pagination. Missing rotations are declared, never concealed.
    A running writer may append after our read: 'atomic' remains false.
    """
    zone, zone_label = _zone(zone_name)
    instant = datetime.now(timezone.utc) if now is None else now
    if (type(instant) is not datetime or instant.tzinfo is None
            or instant.utcoffset() is None):
        raise ValueError("journal_export_clock_invalid")
    day = instant.astimezone(zone).date()
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)
    stop = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)

    def ensure_live():
        if cancelled is not None and cancelled():
            raise InterruptedError("journal_export_cancelled")

    ensure_live()
    events, gaps, active_missing = _events(Path(log_path))
    selected = [event for event in events
                if start <= datetime.fromisoformat(event["timestamp"]) < stop]
    if len(selected) > _MAX_EVENTS:
        raise ValueError("journal_export_limit")
    ensure_live()
    # Do not describe an unreadable rotation as 'available' simply because
    # other rotations contained today's events.
    coverage = "partial" if gaps or active_missing else ("available" if events else "empty")
    document = {
        "schema_version": 1, "format": "cifereca-raw-journal-events",
        "atomic": False, "coverage": coverage,
        "rotation_gaps": gaps, "active_log_missing": active_missing,
        "events": selected,
    }
    output = Path(destination)
    if not output.name.lower().endswith(".zip"):
        raise ValueError("journal_zip_suffix_required")
    if output.exists() or output.is_symlink():
        raise FileExistsError("journal_export_destination_exists")
    if not output.parent.is_dir():
        raise ValueError("journal_export_directory_invalid")

    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=".cifereca-journal-", suffix=".part", dir=output.parent,
        )
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            with ZipFile(stream, mode="w", compression=ZIP_DEFLATED,
                         compresslevel=6, allowZip64=False) as archive:
                digest = sha256()
                written = 0
                with archive.open("cifereca-journal-brut.json", "w") as raw:
                    encoder = json.JSONEncoder(
                        ensure_ascii=False, allow_nan=False, separators=(",", ":"),
                    )
                    for fragment in encoder.iterencode(document):
                        ensure_live()
                        chunk = fragment.encode("utf-8")
                        written += len(chunk)
                        if written > _MAX_JSON_BYTES:
                            raise ValueError("journal_export_limit")
                        raw.write(chunk)
                        digest.update(chunk)
                manifest = {
                    "schema_version": 1, "format": "cifereca-journal-daily-zip",
                    "source": "journal", "date_local": day.isoformat(),
                    "timezone": zone_label,
                    "utc_start": start.isoformat(), "utc_end_exclusive": stop.isoformat(),
                    "event_count": len(selected),
                    "raw_file": "cifereca-journal-brut.json",
                    "raw_bytes": written, "sha256": digest.hexdigest(),
                    "coverage": coverage, "rotation_gaps": gaps,
                    "active_log_missing": active_missing,
                    "atomic": False, "capture": "sequential_retained_log_read",
                    "filter": "local_day_only", "ui_filters_applied": False,
                }
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False),
                )
                archive.writestr(
                    "README.txt",
                    "Cifereca — journal brut du jour (JSON UTF-8).\n"
                    "Les événements proviennent du journal canonique, sans "
                    "masquage ou sélection UI.\n"
                    "L'instantané est non atomique pendant les écritures. "
                    "Voir coverage/rotation_gaps dans manifest.json.\n"
                    "ATTENTION : cet export privé peut contenir des mots de "
                    "passe, tokens, cookies et URLs signées.\n"
                    "Ne pas téléverser automatiquement ; vérifier avant partage.\n",
                )
            stream.flush()
            os.fsync(stream.fileno())
        ensure_live()
        # Hard-link creation is exclusive and atomic: no existing ZIP or
        # symlink may be overwritten, even by a simultaneous second click.
        os.link(tmp_name, output, follow_symlinks=False)
        return output
    finally:
        if tmp_name is not None:
            Path(tmp_name).unlink(missing_ok=True)
