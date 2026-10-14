#!/usr/bin/env python3
"""Bounded, explicit HTTPS qualification of the installed youth plugins.

Offline unless --confirm-live is provided. No mocks, crawler or user profile.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "milan": {
        "package": "youth-source-milan", "module": "milan_youth.py",
        "plugin": "youth.source.milan", "host": "www.editionsmilan.com",
        "max_requests": 9,
    },
    "ecole-loisirs": {
        "package": "youth-source-ecole-loisirs",
        "module": "ecole_loisirs_youth.py",
        "plugin": "youth.source.ecole-loisirs",
        "host": "www.ecoledesloisirs.fr",
        "max_requests": 1,
    },
}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, choices=tuple(SOURCES))
    p.add_argument("--age", type=int, default=8)
    p.add_argument("--limit", type=int, default=2)
    p.add_argument("--timeout", type=float, default=8.0)
    p.add_argument("--confirm-live", action="store_true")
    return p


def plan(args: argparse.Namespace) -> dict:
    if not 0 <= args.age <= 17:
        raise ValueError("age must be in 0..17")
    if not 1 <= args.limit <= 3:
        raise ValueError("limit must be in 1..3")
    if not 2.0 <= args.timeout <= 15.0:
        raise ValueError("timeout must be in 2..15 seconds")
    choice = SOURCES[args.source]
    return {"source": args.source, "age": args.age, "limit": args.limit,
            "timeout_seconds": args.timeout, "request_budget": choice["max_requests"],
            "allowed_host": choice["host"]}


def _valid_isbn13(value: str) -> bool:
    """Only a checksum-valid ISBN Bookland (978/979), not any GTIN/EAN-13."""
    if not re.fullmatch(r"(?:978|979)[0-9]{10}", value):
        return False
    return sum(int(digit) * (1 if index % 2 == 0 else 3)
               for index, digit in enumerate(value)) % 10 == 0


def evidence_for(books: tuple, source_id: str) -> list[dict]:
    evidence = []
    for book in books:
        provenance = tuple(getattr(book, "provenance", ()) or ())
        if source_id not in provenance:
            continue
        ranges = []
        for age in getattr(book, "age_ranges", ()) or ():
            kind = getattr(age, "age_kind", "")
            label = getattr(age, "source_label", "")
            lo = getattr(age, "age_min", None)
            if getattr(age, "source_id", "") != source_id or not label or lo is None:
                continue
            if kind not in {"recommended", "interest", "reading", "publisher"}:
                continue
            ranges.append({"kind": kind, "minimum": lo,
                           "maximum": getattr(age, "age_max", None),
                           "published_label": label})
        if not ranges:
            continue
        identifiers = getattr(book, "identifiers", {}) or {}
        isbn = str(identifiers.get("isbn", ""))
        url = str(getattr(book, "notice_url", "") or "")
        if not url.startswith("https://") or urlsplit(url).hostname != SOURCES[source_id]["host"]:
            continue
        # Publisher HTML can call a toy's valid EAN-13 an "ISBN". The
        # operator-facing PASS must never accept it, even if an adapter
        # accidentally passes a non-ISBN through catalogue normalization.
        if source_id in {"milan", "ecole-loisirs"} and not _valid_isbn13(isbn):
            continue
        evidence.append({"title": str(getattr(book, "title", ""))[:160],
                         "isbn13": isbn, "notice_url": url,
                         "ages": ranges, "provenance": list(provenance)})
    return evidence


def qualification_status(https_requests: list[str], notices: list[dict]) -> str:
    # An installed source may return cached or synthetic records. Neither is
    # evidence of a live HTTPS qualification without an observed request.
    if not https_requests:
        return "BLOCKED"
    return "PASS" if notices else "NO_VERIFIED_NOTICE"


# Only allow bounded structural metadata through the operator-facing JSON.
# Never publish arbitrary plugin diagnostic values, response bodies or URLs.
_MILAN_DIAGNOSTIC_COUNTS = frozenset({
    "page", "requested_limit", "detail_requests", "http_status",
    "html_bytes", "anchor_count", "milan_catalogue_href_count",
    "product_href_count", "product_link_text_count",
    "product_link_accessible_count", "product_link_unnamed_count",
    "unrecognized_catalogue_href_count", "recognized_product_count",
    "qualified_items",
})
_MILAN_DIAGNOSTIC_FLAGS = frozenset({
    "age_filter_present", "content_type_is_html", "next_cursor_present",
})
_MILAN_DIAGNOSTIC_PHASES = frozenset({"listing", "products", "complete"})

# Only metrics already emitted by the public l'école des loisirs plugin.
# Never export raw HTML, search terms, URLs, titles, cookies or headers.
_ECOLE_DIAGNOSTIC_COUNTS = frozenset({
    "page", "requested_limit", "request_count", "http_status",
    "html_bytes", "anchor_count", "product_url_count", "returned_items",
    "qualified_age_count", "unqualified_age_count", "duration_ms",
})
_ECOLE_DIAGNOSTIC_FLAGS = frozenset({
    "text_filter_present", "content_type_is_html", "next_cursor_present",
})
_ECOLE_DIAGNOSTIC_PHASES = frozenset({"listing", "parsing", "complete"})
_ECOLE_DIAGNOSTIC_STATES = frozenset({"items", "empty_unverified", "error"})
_SAFE_ERROR_TYPES = frozenset({"RuntimeError", "ValueError", "TypeError", "ProviderError", "ConnectError", "ReadTimeout", "ConnectTimeout", "HTTPStatusError", "TimeoutError"})


def plugin_diagnostic_for(source: object, source_id: str) -> dict[str, object]:
    """Return only whitelisted local diagnostics; no extra HTTP requests.

    The snapshot may describe a failed call. Neither an HTTP 200 nor a
    populated diagnostic can convert BLOCKED/NO_VERIFIED_NOTICE to PASS.
    """
    if source_id not in {"milan", "ecole-loisirs"}:
        return {}
    snapshot = getattr(source, "diagnostic_snapshot", None)
    if not callable(snapshot):
        return {}
    try:
        raw = snapshot()
    except Exception:
        return {}
    if not isinstance(raw, Mapping) or raw.get("source") != source_id:
        return {}
    result: dict[str, object] = {"source": source_id}
    if source_id == "ecole-loisirs":
        for key in _ECOLE_DIAGNOSTIC_COUNTS:
            value = raw.get(key)
            if type(value) is int and 0 <= value <= 100_000_000:
                result[key] = value
        for key in _ECOLE_DIAGNOSTIC_FLAGS:
            value = raw.get(key)
            if type(value) is bool:
                result[key] = value
        phase = raw.get("phase")
        if isinstance(phase, str) and phase in _ECOLE_DIAGNOSTIC_PHASES:
            result["phase"] = phase
        state = raw.get("state")
        if isinstance(state, str) and state in _ECOLE_DIAGNOSTIC_STATES:
            result["state"] = state
        digest = raw.get("html_sha256_prefix")
        if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{16}", digest):
            result["html_sha256_prefix"] = digest
        error_type = raw.get("error_type")
        if type(error_type) is str and error_type in _SAFE_ERROR_TYPES:
            result["error_type"] = error_type
        return result
    for key in _MILAN_DIAGNOSTIC_COUNTS:
        value = raw.get(key)
        if type(value) is int and 0 <= value <= 100_000_000:
            result[key] = value
    for key in _MILAN_DIAGNOSTIC_FLAGS:
        value = raw.get(key)
        if type(value) is bool:
            result[key] = value
    phase = raw.get("phase")
    if isinstance(phase, str) and phase in _MILAN_DIAGNOSTIC_PHASES:
        result["phase"] = phase
    digest = raw.get("html_sha256_prefix")
    if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{16}", digest):
        result["html_sha256_prefix"] = digest
    error_type = raw.get("error_type")
    if type(error_type) is str and error_type in _SAFE_ERROR_TYPES:
        result["error_type"] = error_type
    return result


def public_report(report: Mapping) -> dict[str, object]:
    """Only explicit schema fields may reach stdout / GitHub Actions.

    Untrusted publisher/provider strings must not be serialised even when a
    future caller mistakenly inserts them into the in-memory report.
    """
    states = {"PASS", "BLOCKED", "NO_VERIFIED_NOTICE", "NOT_RUN", "INVALID_INPUT"}
    result = report.get("result")
    safe: dict[str, object] = {
        "result": result if type(result) is str and result in states else "BLOCKED"
    }
    source = report.get("source")
    if type(source) is not str or source not in SOURCES:
        return safe
    safe["source"] = source
    safe["allowed_host"] = SOURCES[source]["host"]
    if report.get("source_plugin_id") == SOURCES[source]["plugin"]:
        safe["source_plugin_id"] = SOURCES[source]["plugin"]
    for name, bound in (
        ("age", 17), ("limit", 3), ("accepted", 1_000_000),
        ("rejected", 1_000_000), ("ingest_error_count", 1_000_000),
        ("qualified_notice_count", 1_000_000),
        ("requests_count", SOURCES[source]["max_requests"]),
    ):
        value = report.get(name)
        if type(value) is int and 0 <= value <= bound:
            safe[name] = value
    if type(report.get("next_cursor_present")) is bool:
        safe["next_cursor_present"] = report["next_cursor_present"]
    budget = report.get("request_budget")
    if type(budget) is int and budget == SOURCES[source]["max_requests"]:
        safe["request_budget"] = budget
    timeout = report.get("timeout_seconds")
    if type(timeout) in (int, float) and 2 <= timeout <= 15:
        safe["timeout_seconds"] = timeout
    timestamp = report.get("timestamp_utc")
    if type(timestamp) is str and re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?\+00:00",
        timestamp,
    ):
        safe["timestamp_utc"] = timestamp
    commit = report.get("source_commit")
    if type(commit) is str and (commit == "unknown" or re.fullmatch(r"[0-9a-f]{40}", commit)):
        safe["source_commit"] = commit
    version = report.get("core_version")
    if type(version) is str and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}[A-Za-z0-9.+_-]{0,48}", version):
        safe["core_version"] = version
    error_code = report.get("error_code")
    if type(error_code) is str and error_code in {
        "invalid_parameters", "consent_required", "no_public_https_request",
        "timeout", "transport_error", "http_error", "execution_failed",
        "invalid_evidence",
    }:
        safe["error_code"] = error_code
    raw_diagnostic = report.get("plugin_diagnostic")
    if isinstance(raw_diagnostic, dict):
        diagnostic = plugin_diagnostic_for(
            SimpleNamespace(diagnostic_snapshot=lambda: raw_diagnostic), source,
        )
        if diagnostic:
            safe["plugin_diagnostic"] = diagnostic
    return safe


def bundle(name: str, files: tuple[str, ...], workspace: Path) -> Path:
    source = ROOT / "src/plugins" / name
    archive = workspace / (name + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for file in files:
            z.write(source / file, arcname=file)
    return archive


def qualify(args: argparse.Namespace, report: dict) -> None:
    import httpx
    from cifereca import __version__ as core_version
    from cifereca.core import CiferecaCore

    choice = SOURCES[args.source]
    requests: list[str] = []
    report["core_version"] = core_version
    report["source_plugin_id"] = choice["plugin"]
    with tempfile.TemporaryDirectory(prefix="cifereca-youth-qualification-") as work:
        workspace = Path(work)
        # The transient profile is configured before opening the Core.
        for key, directory in (
            ("HOME", "home"), ("XDG_CONFIG_HOME", "config"),
            ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
            ("XDG_STATE_HOME", "state"),
        ):
            folder = workspace / directory
            folder.mkdir()
            os.environ[key] = str(folder)

        core = CiferecaCore.open(
            no_plugins=False,
            force_plugins=("catalog.youth", choice["plugin"]),
            activate_plugins=False,
        )
        archives = (
            ("youth-catalog",
             ("cifereca-plugin.json", "youth_catalog.py", "youth_page.py")),
            (choice["package"], ("cifereca-plugin.json", choice["module"])),
        )
        for name, names in archives:
            core.plugins.install(bundle(name, names, workspace))

        catalog = core.plugins.service("catalog.youth")
        source = core.plugins.service(choice["plugin"])
        if catalog is None or source is None:
            raise RuntimeError("installed source/catalog service unavailable")

        def count_request(req):
            if req.url.scheme != "https" or req.url.host != choice["host"]:
                raise RuntimeError("request outside public HTTPS allowlist")
            if len(requests) >= choice["max_requests"]:
                raise RuntimeError("live request budget exhausted")
            # Never retain query strings, signed URLs or acquisition paths.
            requests.append(choice["host"])

        source.client.close()
        client = httpx.Client(
            timeout=args.timeout, follow_redirects=False, trust_env=False,
            headers={"User-Agent": "Cifereca-Youth-Qualification/0.1",
                     "Accept": "text/html,application/xhtml+xml",
                     "Accept-Language": "fr,en-GB;q=0.8"},
            event_hooks={"request": [count_request]},
        )
        source.client = client
        try:
            if args.source == "ecole-loisirs":
                # Public advanced search already exposes the published age,
                # ISBN and public notice link on the first page. Exactly ONE
                # GET, no product-page fetch, no pagination or open crawler.
                raw = source.search("loup", cursor=None, limit=40)
            else:
                raw = source.browse(age=args.age, themes=(), text_filter="",
                                    cursor=None, limit=args.limit)
            if not isinstance(raw, dict):
                raise RuntimeError("source page is not a mapping")
            result = catalog.ingest(
                "qualification-only", args.source, raw.get("items", ()),
                replace_source=True,
                fresh_for_seconds=raw.get("fresh_for_seconds", 0),
            )
            cached = catalog.search_cached(
                "qualification-only", source_id=args.source,
                **({"age": args.age} if args.source == "ecole-loisirs" else {}),
            )
            notices = evidence_for(cached, args.source)
            report.update({
                "accepted": result.accepted, "rejected": result.rejected,
                "ingest_error_count": len(result.errors),
                "next_cursor_present": bool(raw.get("next_cursor")),
                "qualified_notice_count": min(len(notices), args.limit),
                "result": qualification_status(requests, notices),
            })
            if not requests:
                report["error_code"] = "no_public_https_request"
        finally:
            # The transient Core profile is deleted after this call. Capture
            # only whitelisted diagnostic counters before it disappears,
            # including when browse()/ingest() raised an exception.
            diagnostic = plugin_diagnostic_for(source, args.source)
            if diagnostic:
                report["plugin_diagnostic"] = diagnostic
            client.close()
            report["requests_count"] = len(requests)


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        settings = plan(args)
    except ValueError as exc:
        print(json.dumps({"result": "INVALID_INPUT", "error_code": "invalid_parameters"}, ensure_ascii=False))
        return 2
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), **settings}
    if not args.confirm_live:
        report.update(result="NOT_RUN", error_code="consent_required")
        print(json.dumps(public_report(report), ensure_ascii=False, indent=2))
        return 2
    try:
        report["source_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=3
        ).strip()
    except (OSError, subprocess.SubprocessError):
        report["source_commit"] = "unknown"
    try:
        qualify(args, report)
    except Exception as exc:
        name = type(exc).__name__
        error_code = ("timeout" if name in {"TimeoutError", "ReadTimeout", "ConnectTimeout", "PoolTimeout"}
                      else "transport_error" if name in {"ConnectError", "NetworkError", "ProxyError"}
                      else "http_error" if name == "HTTPStatusError"
                      else "execution_failed")
        report.update(result="BLOCKED", error_code=error_code)
    print(json.dumps(public_report(report), ensure_ascii=False, indent=2))
    return 0 if report.get("result") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
