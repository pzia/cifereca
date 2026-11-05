"""Version 1: pure-stdlib contract for private canonical daily-journal ZIPs.

The Core producer and diagnostic consumers share this format. No provider,
GTK, SQLite, application configuration, network I/O or ZIP extraction occurs.
The SHA-256 verifies integrity against the manifest, NOT publisher identity.
"""
from __future__ import annotations

from hashlib import sha256
import hmac
import json
from pathlib import Path
import re
import stat
import zipfile

JOURNAL_ZIP_SCHEMA_VERSION = 1
JOURNAL_ZIP_FORMAT = "cifereca-journal-daily-zip"
RAW_JOURNAL_FORMAT = "cifereca-raw-journal-events"
RAW_MEMBER = "cifereca-journal-brut.json"
MANIFEST_MEMBER = "manifest.json"
README_MEMBER = "README.txt"
DAILY_MEMBERS = frozenset((RAW_MEMBER, MANIFEST_MEMBER, README_MEMBER))
MAX_MANIFEST_BYTES = 64 * 1024
MAX_README_BYTES = 16 * 1024
MAX_READER_JSON_BYTES = 64 * 1024 * 1024
MAX_READER_EVENTS = 150_000
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class JournalZipError(ValueError):
    """Closed error reason; never includes archive contents or private paths."""


def daily_journal_manifest(
    *, date_local: str, timezone: str, utc_start: str,
    utc_end_exclusive: str, event_count: int, raw_bytes: int,
    sha256_hex: str, coverage: str, rotation_gaps: list,
    active_log_missing: bool,
) -> dict:
    """Emit the versioned canonical manifest without changing legacy v1 fields."""
    return {
        "schema_version": JOURNAL_ZIP_SCHEMA_VERSION,
        "format": JOURNAL_ZIP_FORMAT,
        "source": "journal",
        "date_local": date_local,
        "timezone": timezone,
        "utc_start": utc_start,
        "utc_end_exclusive": utc_end_exclusive,
        "event_count": event_count,
        "raw_file": RAW_MEMBER,
        "raw_bytes": raw_bytes,
        "sha256": sha256_hex,
        "coverage": coverage,
        "rotation_gaps": rotation_gaps,
        "active_log_missing": active_log_missing,
        "atomic": False,
        "capture": "sequential_retained_log_read",
        "filter": "local_day_only",
        "ui_filters_applied": False,
    }


def _read_member(archive: zipfile.ZipFile, member: zipfile.ZipInfo,
                 limit: int) -> bytes:
    # Guard before decompression, then bound the actual bytes received.
    if not 2 <= member.file_size <= limit:
        raise JournalZipError("journal_too_large")
    with archive.open(member) as stream:
        result = stream.read(limit + 1)
    if len(result) != member.file_size or len(result) > limit:
        raise JournalZipError("journal_too_large")
    return result


def _manifest_valid(manifest: object, *, limit: int, max_events: int) -> bool:
    return (
        type(manifest) is dict
        and manifest.get("format") == JOURNAL_ZIP_FORMAT
        and type(manifest.get("schema_version")) is int
        and manifest["schema_version"] == JOURNAL_ZIP_SCHEMA_VERSION
        and manifest.get("raw_file") == RAW_MEMBER
        and type(manifest.get("raw_bytes")) is int
        and 2 <= manifest["raw_bytes"] <= limit
        and type(manifest.get("event_count")) is int
        and 0 <= manifest["event_count"] <= max_events
        and type(manifest.get("sha256")) is str
        and _HEX.fullmatch(manifest["sha256"]) is not None
    )


def read_raw_journal_zip(
    path: Path, *, allow_legacy: bool = True,
    max_json_bytes: int = MAX_READER_JSON_BYTES,
    max_events: int = MAX_READER_EVENTS,
    include_manifest_coverage: bool = False,
) -> dict | tuple[dict, dict | None]:
    """Read and integrity-check a v1 daily ZIP (or historical single JSON ZIP).

    Never extract ZIP entries, follow embedded paths, call Core or initiate
    network I/O. The default return is the raw journal unchanged. Diagnostic
    callers may explicitly request a (raw, coverage_metadata) pair; the
    coverage metadata comes ONLY from the validated daily ZIP's manifest,
    never from untrusted event JSON, and is None for legacy single-file ZIPs.
    No rotation filenames or unrelated manifest fields are returned.
    The caller must redact its own reports and never republish raw events.
    """
    if type(include_manifest_coverage) is not bool:
        raise JournalZipError("journal_limits_invalid")
    if (type(max_json_bytes) is not int or not 2 <= max_json_bytes <= MAX_READER_JSON_BYTES
            or type(max_events) is not int or not 0 <= max_events <= MAX_READER_EVENTS):
        raise JournalZipError("journal_limits_invalid")
    manifest = None
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if (len(names) != len(set(names))
                    or any(member.is_dir() or "/" in member.filename
                           or "\\" in member.filename or member.flag_bits & 1
                           or stat.S_IFMT(member.external_attr >> 16) == stat.S_IFLNK
                           for member in members)):
                raise JournalZipError("unexpected_journal_archive")
            by_name = {member.filename: member for member in members}
            if set(names) == DAILY_MEMBERS:
                manifest_raw = _read_member(
                    archive, by_name[MANIFEST_MEMBER], MAX_MANIFEST_BYTES
                )
                _read_member(archive, by_name[README_MEMBER], MAX_README_BYTES)
                manifest = json.loads(manifest_raw)
                if not _manifest_valid(manifest, limit=max_json_bytes,
                                       max_events=max_events):
                    raise JournalZipError("invalid_journal_manifest")
                raw = _read_member(archive, by_name[RAW_MEMBER], max_json_bytes)
                if len(raw) != manifest["raw_bytes"]:
                    raise JournalZipError("journal_size_mismatch")
                if not hmac.compare_digest(sha256(raw).hexdigest(), manifest["sha256"]):
                    raise JournalZipError("journal_digest_mismatch")
            elif allow_legacy and len(members) == 1 and names[0].endswith(".json"):
                raw = _read_member(archive, members[0], max_json_bytes)
            else:
                raise JournalZipError("unexpected_journal_archive")
        document = json.loads(raw)
    except JournalZipError:
        raise
    except (OSError, zipfile.BadZipFile, UnicodeError, json.JSONDecodeError,
            RuntimeError, RecursionError, EOFError, ValueError,
            NotImplementedError) as exc:
        raise JournalZipError("unreadable_journal") from exc
    if (type(document) is not dict
            or document.get("format") != RAW_JOURNAL_FORMAT
            or type(document.get("schema_version")) is not int
            or document["schema_version"] != JOURNAL_ZIP_SCHEMA_VERSION
            or type(document.get("events")) is not list):
        raise JournalZipError("unsupported_journal_format")
    if len(document["events"]) > max_events:
        raise JournalZipError("too_many_events")
    if manifest is not None and len(document["events"]) != manifest["event_count"]:
        raise JournalZipError("journal_event_count_mismatch")
    if include_manifest_coverage:
        # Avoid republishing private rotation names; the bound manifest
        # provides only a closed marker for whether gaps were declared.
        coverage = (
            {
                "coverage": manifest.get("coverage"),
                "rotation_gaps_present": (
                    bool(manifest["rotation_gaps"])
                    if type(manifest.get("rotation_gaps")) is list else None
                ),
                "active_log_missing": manifest.get("active_log_missing"),
            }
            if manifest is not None else None
        )
        return document, coverage
    return document
