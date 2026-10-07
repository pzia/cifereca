from __future__ import annotations

import json
import os
import platform
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from cifereca.plugin_api import UiPageContribution

SENSITIVE = ("password", "passwd", "token", "secret", "cookie", "authorization", "session", "barcode", "readerid")


def _xdg(env: str, fallback: Path) -> Path:
    return Path(os.environ.get(env, fallback)).expanduser()


def _config_path() -> Path:
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config") / "cifereca" / "config.json"


def _data_root() -> Path:
    return _xdg("XDG_DATA_HOME", Path.home() / ".local" / "share") / "cifereca"


def _state_root() -> Path:
    return _xdg("XDG_STATE_HOME", Path.home() / ".local" / "state") / "cifereca"


def _redact(value: Any, key: str = "") -> Any:
    if any(part in key.casefold() for part in SENSITIVE):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v, key) for v in value]
    if isinstance(value, str):
        lower = value.casefold()
        if any((name + "=") in lower or (name + ":") in lower for name in SENSITIVE):
            return "<redacted>"
        return value[:300] + ("…" if len(value) > 300 else "")
    return value


def _runtime_state() -> dict[str, Any]:
    root = _data_root() / "runtime"
    current = root / "current"
    previous = root / "previous"
    result: dict[str, Any] = {
        "managed": current.is_symlink(),
        "previous_present": previous.is_symlink(),
        "release_count": 0,
        "source": "unknown",
        "repository": "",
        "ref": "",
        "commit": "",
        "installed_at": "",
    }
    releases = root / "releases"
    if releases.is_dir():
        try:
            result["release_count"] = sum(1 for p in releases.iterdir() if p.is_dir())
        except OSError:
            pass
    try:
        target = current.resolve(strict=True)
        marker = target / ".cifereca-install.json"
        if marker.is_file():
            meta = json.loads(marker.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                provenance = meta.get("provenance") if isinstance(meta.get("provenance"), dict) else {}
                result.update({
                    "source": provenance.get("type") or meta.get("source") or "unknown",
                    "repository": provenance.get("repository") or "",
                    "ref": provenance.get("ref") or "",
                    "commit": provenance.get("commit") or "",
                    "installed_at": meta.get("installed_at") or "",
                })
    except Exception as exc:
        result["runtime_error"] = type(exc).__name__
    backups = _state_root() / "update-backups"
    try:
        result["backup_count"] = sum(1 for p in backups.iterdir() if p.is_dir()) if backups.is_dir() else 0
    except OSError:
        result["backup_count"] = 0
    return result


def _database_state() -> dict[str, Any]:
    path = _data_root() / "library.sqlite3"
    result: dict[str, Any] = {"present": path.is_file(), "schema": None, "integrity": "absent"}
    if not path.is_file():
        return result
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=3) as db:
            row = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            result["schema"] = int(row[0]) if row else 0
            result["integrity"] = str(db.execute("PRAGMA integrity_check").fetchone()[0])
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    return result


def _config_state() -> dict[str, Any]:
    path = _config_path()
    result: dict[str, Any] = {"present": path.is_file(), "valid_json": False, "keys": []}
    if not path.is_file():
        return result
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("root_not_object")
        result["valid_json"] = True
        result["keys"] = sorted(str(k) for k in value)
        for name in ("identities", "providers", "accounts", "credentials"):
            item = value.get(name)
            if isinstance(item, (list, dict)):
                result[name] = len(item)
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    return result


def build_report(host, broker) -> dict[str, Any]:
    try:
        status = dict(broker.system_status() or {})
    except Exception as exc:
        status = {"error_type": type(exc).__name__}
    try:
        plugins = list(host.plugin_statuses() or [])
    except Exception as exc:
        plugins = [{"plugin_id": "host.plugin_statuses", "state": "error", "error_type": type(exc).__name__}]
    plugin_rows = []
    attention = 0
    for row in plugins:
        if not isinstance(row, Mapping):
            continue
        state = str(row.get("state") or "unknown")
        if state in {"blocked", "error", "incompatible", "degraded"}:
            attention += 1
        plugin_rows.append(_redact({
            "plugin_id": row.get("plugin_id") or row.get("id") or "",
            "display_name": row.get("display_name") or "",
            "type": row.get("type") or "",
            "version": row.get("version") or "",
            "package_id": row.get("package_id") or "",
            "package_version": row.get("package_version") or "",
            "enabled": bool(row.get("enabled")),
            "state": state,
            "reason": row.get("reason") or "",
            "reason_code": row.get("reason_code") or "",
            "requirements": row.get("requirements") or [],
        }))
    runtime = _runtime_state()
    database = _database_state()
    config = _config_state()
    bad = bool(attention or database.get("error_type") or config.get("error_type"))
    warning = bad or not database.get("present") or database.get("integrity") not in {"ok", "absent"} or not config.get("valid_json")
    return _redact({
        "format": 1,
        "result": "warning" if warning else "ok",
        "core": {
            "version": status.get("version") or "unknown",
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.system() + " " + platform.release(),
        },
        "runtime": runtime,
        "database": database,
        "config": config,
        "plugins": {"count": len(plugin_rows), "attention": attention, "items": plugin_rows},
        "core_status": status,
    })


def render_text(report: Mapping[str, Any]) -> str:
    core = report.get("core", {})
    runtime = report.get("runtime", {})
    db = report.get("database", {})
    cfg = report.get("config", {})
    plugins = report.get("plugins", {})
    lines = [
        "CIFERECA PREFLIGHT DIAGNOSTIC",
        f"RESULT: {str(report.get('result', 'unknown')).upper()}",
        "",
        "[CORE]",
        f"version: {core.get('version')}",
        f"python: {core.get('python')} ({core.get('implementation')})",
        f"platform: {core.get('platform')}",
        "",
        "[RUNTIME]",
        f"managed: {runtime.get('managed')}",
        f"previous_present: {runtime.get('previous_present')}",
        f"release_count: {runtime.get('release_count')}",
        f"source: {runtime.get('source') or '-'}",
        f"repository: {runtime.get('repository') or '-'}",
        f"ref: {runtime.get('ref') or '-'}",
        f"commit: {runtime.get('commit') or '-'}",
        f"backup_count: {runtime.get('backup_count', 0)}",
        "",
        "[DATABASE]",
        f"present: {db.get('present')}",
        f"schema: {db.get('schema')}",
        f"integrity: {db.get('integrity')}",
        f"error_type: {db.get('error_type') or '-'}",
        "",
        "[CONFIG]",
        f"present: {cfg.get('present')}",
        f"valid_json: {cfg.get('valid_json')}",
        f"keys: {', '.join(cfg.get('keys') or []) or '-'}",
        "",
        "[PLUGINS]",
        f"count: {plugins.get('count', 0)}",
        f"attention: {plugins.get('attention', 0)}",
    ]
    for row in plugins.get("items", []):
        lines.append(
            f"- {row.get('plugin_id') or row.get('display_name')} "
            f"package={row.get('package_id') or '-'} version={row.get('version') or row.get('package_version') or '-'} "
            f"enabled={row.get('enabled')} state={row.get('state')} reason={row.get('reason_code') or row.get('reason') or '-'}"
        )
        reqs = row.get("requirements") or []
        for req in reqs:
            if isinstance(req, Mapping) and not req.get("ok", True):
                lines.append(f"  requirement: {_redact(dict(req))}")
    return "\n".join(lines).rstrip() + "\n"


class DiagnosticsPreflightPlugin:
    def __init__(self) -> None:
        self.context = None
        self.diagnostics = None

    def activate(self, context):
        self.context = context
        self.diagnostics = context.broker("diagnostics")
        if self.diagnostics is None:
            raise RuntimeError("broker diagnostics indisponible")
        return self

    def pages(self):
        return [UiPageContribution(
            route="diagnostics-preflight",
            title="Prédiagnostic",
            icon_name="utilities-system-monitor-symbolic",
            order=94,
            factory=self._build,
            group="configuration",
        )]

    def _build(self, host):
        from gi.repository import Gdk, Gtk
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for margin in ("top", "bottom", "start", "end"):
            getattr(outer, "set_margin_" + margin)(18)
        title = Gtk.Label(label="Prédiagnostic autonome", xalign=0)
        title.add_css_class("heading")
        outer.append(title)
        hint = Gtk.Label(label="Rapport local en lecture seule, expurgé, compatible avec le Core initial 0.9.44+dev.1.", xalign=0)
        hint.set_wrap(True)
        outer.append(hint)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        refresh = Gtk.Button(label="Actualiser")
        copy = Gtk.Button(label="Copier")
        actions.append(refresh)
        actions.append(copy)
        outer.append(actions)
        view = Gtk.TextView()
        view.set_editable(False)
        view.set_cursor_visible(False)
        view.set_monospace(True)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_vexpand(True)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(view)
        scroll.set_vexpand(True)
        outer.append(scroll)
        state = {"text": ""}

        def render(*_args):
            try:
                state["text"] = render_text(build_report(host, self.diagnostics))
                view.get_buffer().set_text(state["text"])
            except Exception as exc:
                state["text"] = f"CIFERECA PREFLIGHT DIAGNOSTIC\nRESULT: ERROR\nerror_type: {type(exc).__name__}\n"
                view.get_buffer().set_text(state["text"])

        def copy_report(_button):
            try:
                display = Gdk.Display.get_default()
                if display is None:
                    raise RuntimeError("display indisponible")
                display.get_clipboard().set(state["text"])
                host.toast("Prédiagnostic copié")
            except Exception as exc:
                host.toast(f"Copie impossible : {type(exc).__name__}")

        refresh.connect("clicked", render)
        copy.connect("clicked", copy_report)
        render()
        return outer

    def deactivate(self):
        self.diagnostics = None
        self.context = None


PLUGIN = DiagnosticsPreflightPlugin()
