#!/usr/bin/env python3
"""Bounded, explicit HTTPS qualification of the installed youth plugins.

Offline unless --confirm-live is provided. No mocks, crawler or user profile.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "booktrust": {
        "package": "youth-source-booktrust", "module": "booktrust_youth.py",
        "plugin": "youth.source.booktrust", "host": "www.booktrust.org.uk",
        "max_requests": 1,
    },
    "milan": {
        "package": "youth-source-milan", "module": "milan_youth.py",
        "plugin": "youth.source.milan", "host": "www.editionsmilan.com",
        "max_requests": 9,
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


def evidence_for(books: tuple, source_id: str) -> list[dict]:
    evidence = []
    for book in books:
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
        if source_id == "milan" and not (len(isbn) == 13 and isbn.isdigit()):
            continue
        evidence.append({"title": str(getattr(book, "title", ""))[:160],
                         "isbn13": isbn, "notice_url": url,
                         "ages": ranges, "provenance": list(getattr(book, "provenance", ()) or ())})
    return evidence


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
            requests.append(str(req.url))

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
            raw = source.browse(age=args.age, themes=(), text_filter="",
                                cursor=None, limit=args.limit)
            if not isinstance(raw, dict):
                raise RuntimeError("source page is not a mapping")
            result = catalog.ingest(
                "qualification-only", args.source, raw.get("items", ()),
                replace_source=True,
                fresh_for_seconds=raw.get("fresh_for_seconds", 0),
            )
            cached = catalog.search_cached("qualification-only", source_id=args.source)
            notices = evidence_for(cached, args.source)
            report.update({
                "accepted": result.accepted, "rejected": result.rejected,
                "ingest_errors": list(result.errors),
                "next_cursor_present": bool(raw.get("next_cursor")),
                "qualified_notices": notices[:args.limit],
                "result": "PASS" if notices else "NO_VERIFIED_NOTICE",
            })
        finally:
            client.close()
            report["https_requests"] = requests
            report["requests_count"] = len(requests)


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        settings = plan(args)
    except ValueError as exc:
        print(json.dumps({"result": "INVALID_INPUT", "reason": str(exc)}, ensure_ascii=False))
        return 2
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), **settings}
    if not args.confirm_live:
        report.update(result="NOT_RUN", reason="--confirm-live required for public HTTPS")
        print(json.dumps(report, ensure_ascii=False, indent=2))
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
        report.update(result="BLOCKED", error_type=type(exc).__name__, reason=str(exc)[:300])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("result") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
