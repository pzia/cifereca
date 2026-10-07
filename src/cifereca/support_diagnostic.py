from __future__ import annotations

import json
import platform
import sqlite3
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .diagnostics import redact
from .paths import config_file, database_file
from .storage import CURRENT_SCHEMA_VERSION


def _release_metadata() -> dict[str, Any]:
    for parent in Path(__file__).resolve().parents:
        marker = parent / ".cifereca-install.json"
        if marker.is_file():
            try:
                value = json.loads(marker.read_text(encoding="utf-8"))
                return value if isinstance(value, dict) else {}
            except (OSError, ValueError, TypeError):
                return {}
    return {}


def _database_state() -> dict[str, Any]:
    path = database_file()
    result: dict[str, Any] = {
        "present": path.is_file(),
        "expected_schema": CURRENT_SCHEMA_VERSION,
    }
    if not path.is_file():
        result.update({"status": "warning", "schema": None, "integrity": "absent"})
        return result
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as db:
            row = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            schema = int(row[0]) if row is not None else 0
            integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
            migration_rows = int(db.execute(
                "SELECT COUNT(*) FROM metadata WHERE lower(key) LIKE '%migration%' OR lower(key) LIKE '%journal%'"
            ).fetchone()[0])
        result.update({
            "schema": schema,
            "integrity": integrity,
            "migration_markers": migration_rows,
            "status": "ok" if schema == CURRENT_SCHEMA_VERSION and integrity == "ok" else "warning",
        })
    except Exception as exc:
        result.update({"status": "error", "error_type": type(exc).__name__})
    return result


def _config_state() -> dict[str, Any]:
    path = config_file()
    result: dict[str, Any] = {"present": path.is_file()}
    if not path.is_file():
        result.update({"status": "warning", "keys": []})
        return result
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("configuration root must be object")
        identities = value.get("identities")
        providers = value.get("providers")
        result.update({
            "status": "ok",
            "keys": sorted(str(k) for k in value),
            "identities": len(identities) if isinstance(identities, (list, dict)) else None,
            "providers": len(providers) if isinstance(providers, (list, dict)) else None,
        })
    except Exception as exc:
        result.update({"status": "error", "error_type": type(exc).__name__})
    return result


def _plugin_state(manager: Any, *, verbose: bool) -> dict[str, Any]:
    if manager is None:
        return {"status": "error", "reason": "plugin_manager_unavailable", "plugins": []}
    try:
        rows = list(manager.diagnostic())
    except Exception as exc:
        return {"status": "error", "reason": type(exc).__name__, "plugins": []}

    compact = []
    attention = 0
    for row in rows:
        state = str(row.get("state") or "unknown")
        enabled = bool(row.get("enabled"))
        if state in {"blocked", "error", "incompatible", "degraded"}:
            attention += 1
        item = {
            "plugin_id": row.get("plugin_id"),
            "package_id": row.get("package_id"),
            "version": row.get("version"),
            "package_version": row.get("package_version"),
            "enabled": enabled,
            "state": state,
            "reason_code": row.get("reason_code") or "",
        }
        if verbose:
            item["requirements"] = row.get("requirements") or []
            item["requires"] = row.get("requires") or []
            item["requires_contracts"] = row.get("requires_contracts") or []
            item["error"] = row.get("error") or ""
        compact.append(redact(item))
    return {
        "status": "warning" if attention else "ok",
        "count": len(compact),
        "attention": attention,
        "plugins": compact,
    }


def build_support_report(service: Any, *, verbose: bool = False) -> dict[str, Any]:
    install = _release_metadata()
    manager = getattr(service, "plugin_manager", None) or getattr(service, "_plugin_manager", None)
    db = _database_state()
    cfg = _config_state()
    plugins = _plugin_state(manager, verbose=verbose)

    checks: list[dict[str, Any]] = []
    try:
        from .brokers import DiagnosticsBrokerImpl
        for item in DiagnosticsBrokerImpl(service).active_checks():
            checks.append(redact({
                "check_id": getattr(item, "check_id", ""),
                "component": getattr(item, "component", ""),
                "status": getattr(item, "status", "unknown"),
                "message": getattr(item, "message", ""),
                "detail": dict(getattr(item, "detail", {}) or {}),
                "scope": getattr(item, "scope", ""),
            }))
    except Exception as exc:
        checks.append({"check_id": "core.active_checks", "status": "error", "error_type": type(exc).__name__})

    statuses = [
        str(db.get("status") or "unknown"),
        str(cfg.get("status") or "unknown"),
        str(plugins.get("status") or "unknown"),
        *[str(row.get("status") or "unknown") for row in checks],
    ]
    overall = "error" if "error" in statuses else ("warning" if "warning" in statuses else "ok")
    provenance = install.get("provenance") if isinstance(install.get("provenance"), dict) else {}

    report = {
        "format": 1,
        "result": overall,
        "core": {
            "version": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "implementation": platform.python_implementation(),
            "source": provenance.get("type") or "unknown",
            "repository": provenance.get("repository") or "",
            "ref": provenance.get("ref") or "",
            "commit": provenance.get("commit") or "",
            "installed_at": install.get("installed_at") or "",
        },
        "database": db,
        "config": cfg,
        "plugins": plugins,
        "checks": checks,
    }
    return redact(report)


def support_report_text(service: Any, *, verbose: bool = False) -> str:
    report = build_support_report(service, verbose=verbose)
    core = report["core"]
    db = report["database"]
    cfg = report["config"]
    plugins = report["plugins"]
    lines = [
        "CIFERECA SUPPORT DIAGNOSTIC",
        f"RESULT: {str(report['result']).upper()}",
        "",
        "[CORE]",
        f"version: {core.get('version')}",
        f"python: {core.get('python')} ({core.get('implementation')})",
        f"platform: {core.get('platform')}",
        f"source: {core.get('source')}",
        f"repository: {core.get('repository') or '-'}",
        f"ref: {core.get('ref') or '-'}",
        f"commit: {core.get('commit') or '-'}",
        f"installed_at: {core.get('installed_at') or '-'}",
        "",
        "[DATABASE]",
        f"status: {db.get('status')}",
        f"schema: {db.get('schema')} / expected {db.get('expected_schema')}",
        f"integrity: {db.get('integrity')}",
        f"migration_markers: {db.get('migration_markers', 0)}",
        "",
        "[CONFIG]",
        f"status: {cfg.get('status')}",
        f"keys: {', '.join(cfg.get('keys') or []) or '-'}",
        f"identities: {cfg.get('identities')}",
        f"providers: {cfg.get('providers')}",
        "",
        "[PLUGINS]",
        f"status: {plugins.get('status')}",
        f"count: {plugins.get('count', 0)}",
        f"attention: {plugins.get('attention', 0)}",
    ]
    for row in plugins.get("plugins") or []:
        lines.append(
            f"- {row.get('plugin_id')} package={row.get('package_id')} "
            f"version={row.get('version')} package_version={row.get('package_version')} "
            f"enabled={row.get('enabled')} state={row.get('state')} "
            f"reason={row.get('reason_code') or '-'}"
        )
        if verbose:
            for key in ("requirements", "requires", "requires_contracts", "error"):
                value = row.get(key)
                if value:
                    lines.append(f"  {key}: {json.dumps(value, ensure_ascii=False)}")
    lines += ["", "[CHECKS]"]
    for row in report.get("checks") or []:
        lines.append(
            f"- {row.get('status', 'unknown').upper()} {row.get('check_id')}: "
            f"{row.get('message') or row.get('component') or ''}"
        )
        detail = row.get("detail") or {}
        if detail:
            lines.append(f"  detail: {json.dumps(detail, ensure_ascii=False, sort_keys=True)}")
    return "\n".join(lines).rstrip() + "\n"
