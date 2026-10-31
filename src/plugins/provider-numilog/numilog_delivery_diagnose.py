"""Read-only diagnosis of Numilog delivery attempts from a raw journal ZIP.

No HTTP, SQLite or filesystem mutation. Output contains only closed diagnostic
values: no signed URLs, raw headers, bodies or authentication material. The
report does NOT prove final EPUB placement or an ADEPT result.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
import zipfile

_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_EVENTS = 150_000
_MAX_ACTIONS = 10_000
_TOKEN = re.compile(r"[0-9a-f]{12}\Z")
_CODE = frozenset({
    "started", "redirect", "http_ok", "single_allowed_candidate",
    "html_delivery_link_missing", "html_delivery_ambiguous",
    "session_expired_confirmed", "login_form_detected", "html_error", "html_invalid",
    "http_forbidden", "http_unauthorized", "http_rate_limited", "link_expired",
    "http_error", "origin_rejected", "redirect_loop", "redirect_limit",
    "mime_unexpected", "download_oversize", "artifact_invalid", "xml_not_acsm",
    "adept_attention_required", "adept_unconfirmed", "epub_complete",
    "acsm_complete", "acsm_candidate", "html_oversize", "unclassified_error",
})
_PHASE = frozenset({
    "start", "delivery_http", "html_selection", "artifact_validation",
    "adept_fulfillment", "completed",
})
_ORIGIN = frozenset({
    "acsm_fulfillment", "numilog_site", "numilog_service", "eden", "cdn", "other",
})
_PATH = frozenset({
    "acsm_fulfillment", "readbook", "personal_library", "login_page",
    "confirmation_page", "other",
})
_MIME = frozenset({"html", "acsm_xml", "xml", "epub", "binary", "other"})
_SOURCE = frozenset({"readbook", "owned_card", "direct_url"})


class JournalFormatError(ValueError):
    pass


def load_journal(path: Path) -> dict:
    """Read a single bounded JSON ZIP member without extracting any path."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if (len(members) != 1 or members[0].file_size > _MAX_ARCHIVE_BYTES
                    or members[0].file_size < 2 or not members[0].filename.endswith(".json")):
                raise JournalFormatError("unexpected_journal_archive")
            with archive.open(members[0]) as source:
                raw = source.read(_MAX_ARCHIVE_BYTES + 1)
            if len(raw) > _MAX_ARCHIVE_BYTES:
                raise JournalFormatError("journal_too_large")
        result = json.loads(raw)
    except (OSError, zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, RuntimeError) as exc:
        raise JournalFormatError("unreadable_journal") from exc
    if (type(result) is not dict or result.get("format") != "cifereca-raw-journal-events"
            or result.get("schema_version") != 1 or type(result.get("events")) is not list):
        raise JournalFormatError("unsupported_journal_format")
    if len(result["events"]) > _MAX_EVENTS:
        raise JournalFormatError("too_many_events")
    return result


def _closed(value, accepted, fallback="unknown"):
    return value if type(value) is str and value in accepted else fallback


def _detail(event):
    if type(event) is not dict:
        return None
    if event.get("module") == "NUMILOG" and event.get("operation") == "acquisition_delivery":
        return event.get("detail")
    # Compatibility with nonprojected canonical DIAG_JSON records.
    message = event.get("message")
    if not isinstance(message, str) or not message.startswith("DIAG_JSON ") or len(message) > 65_536:
        return None
    try:
        record = json.loads(message[10:])
    except (json.JSONDecodeError, ValueError):
        return None
    if (type(record) is dict and record.get("category") == "NUMILOG"
            and record.get("operation") == "acquisition_delivery"):
        return record.get("detail")
    return None


def _timestamp(event):
    value = event.get("timestamp")
    return value if type(value) is str and 10 <= len(value) <= 40 and value[:4].isdigit() else ""


def _closed_event(detail):
    event = {
        "phase": _closed(detail.get("phase"), _PHASE),
        "code": _closed(detail.get("code"), _CODE, "unclassified_error"),
    }
    for key, enum in (
        ("origin_kind", _ORIGIN), ("path_kind", _PATH),
        ("redirect_target_origin_kind", _ORIGIN), ("redirect_target_kind", _PATH),
        ("mime", _MIME),
    ):
        if key in detail:
            event[key] = _closed(detail[key], enum)
    status = detail.get("http_status")
    if type(status) is int and 100 <= status <= 599:
        event["http_status"] = status
    hops = detail.get("hops")
    if type(hops) is int and 0 <= hops <= 12:
        event["hops"] = hops
    if type(detail.get("cookie_header_present")) is bool:
        event["cookie_header_present"] = detail["cookie_header_present"]
    return event


def _classification(terminal, last_http):
    if terminal is None:
        return "incomplete_trace"
    code = terminal["code"]
    if code in {"epub_complete", "acsm_complete"}:
        return "delivery_complete_unverified_placement"
    if code == "acsm_candidate":
        return "acsm_candidate_requires_core_adept"
    if code in {"http_error", "http_forbidden", "http_unauthorized", "http_rate_limited", "link_expired"}:
        return "http_refusal" if last_http else "http_refusal_without_hop"
    if code in {"origin_rejected", "redirect_limit", "redirect_loop"}:
        return "redirect_or_origin_guard"
    if code.startswith("html_") or code in {"login_form_detected", "session_expired_confirmed"}:
        return "supplier_html_or_session"
    if code in {"adept_attention_required", "adept_unconfirmed"}:
        return "adept_fulfillment"
    if code in {"artifact_invalid", "xml_not_acsm", "mime_unexpected", "download_oversize"}:
        return "response_or_artifact_invalid"
    return "unclassified_failure"


def analyze(journal: dict, *, task_ref: str = "") -> dict:
    events = journal["events"]
    first = last = ""
    actions = {}
    observations = 0
    for event in events:
        if type(event) is not dict:
            continue
        ts = _timestamp(event)
        if ts:
            if not first or ts < first:
                first = ts
            if not last or ts > last:
                last = ts
        detail = _detail(event)
        if type(detail) is not dict:
            continue
        scoped = event.get("task_ref")
        if task_ref and scoped != task_ref:
            continue
        token = detail.get("action")
        if type(token) is not str or _TOKEN.fullmatch(token) is None:
            continue
        if token not in actions and len(actions) >= _MAX_ACTIONS:
            raise JournalFormatError("too_many_delivery_actions")
        observations += 1
        attempt = actions.setdefault(token, {
            "action": token, "started_at": ts, "last_at": ts,
            "source": _closed(detail.get("source"), _SOURCE),
            "events": [], "terminal": None,
            "task_ref": scoped if type(scoped) is str and len(scoped) <= 64 else None,
        })
        if ts and (not attempt["started_at"] or ts < attempt["started_at"]):
            attempt["started_at"] = ts
        if ts and (not attempt["last_at"] or ts > attempt["last_at"]):
            attempt["last_at"] = ts
        item = _closed_event(detail)
        if item["phase"] == "completed":
            if attempt["terminal"] is not None:
                attempt["duplicate_terminal"] = True
            else:
                attempt["terminal"] = item
        else:
            attempt["events"].append(item)
    reports = []
    counts = Counter()
    for attempt in sorted(actions.values(), key=lambda value: (value["started_at"], value["action"])):
        last_http = next((h for h in reversed(attempt["events"])
                          if h["phase"] == "delivery_http"), None)
        classification = _classification(attempt["terminal"], last_http)
        report = {
            "action": attempt["action"], "task_ref": attempt["task_ref"],
            "source": attempt["source"], "started_at": attempt["started_at"],
            "last_at": attempt["last_at"],
            "terminal_code": attempt["terminal"]["code"] if attempt["terminal"] else None,
            "classification": classification,
            "http_hops": [h for h in attempt["events"] if h["phase"] == "delivery_http"],
            "other_phases": [h for h in attempt["events"] if h["phase"] not in {"start", "delivery_http"}],
        }
        if attempt.get("duplicate_terminal"):
            report["duplicate_terminal"] = True
        counts[classification] += 1
        reports.append(report)
    return {
        "schema_version": 1,
        "coverage": {
            "first_timestamp": first, "last_timestamp": last,
            "journal_events": len(events), "delivery_events": observations,
            "delivery_attempts": len(reports),
            "note": "No terminal event proves a placed EPUB; verify Core OriginLink/Placement/SHA separately.",
        },
        "counts": dict(sorted(counts.items())), "attempts": reports,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", type=Path, help="Export brut ZIP du journal Cifereca")
    parser.add_argument("--task-ref", default="", help="Limiter à un task_ref exact")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if len(args.task_ref) > 64 or "\x00" in args.task_ref:
        parser.error("task_ref invalide")
    try:
        report = analyze(load_journal(args.journal), task_ref=args.task_ref)
    except JournalFormatError as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        coverage = report["coverage"]
        print(f"Journal: {coverage['first_timestamp']} -> {coverage['last_timestamp']}")
        print(f"Livraisons Numilog: {coverage['delivery_attempts']} tentatives, {coverage['delivery_events']} événements")
        for row in report["attempts"]:
            print(f"{row['started_at']} action={row['action']} task={row['task_ref'] or '-'} "
                  f"code={row['terminal_code'] or 'absent'} diagnostic={row['classification']}")
            for hop in row["http_hops"]:
                print(f"  hop={hop.get('hops', '?')} {hop.get('origin_kind', '?')}/"
                      f"{hop.get('path_kind', '?')} HTTP={hop.get('http_status', '?')} "
                      f"-> {hop.get('redirect_target_origin_kind', '-')}")
        print("Attention: acquisition HTTP != preuve d'un EPUB placé et rattaché au profil.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
