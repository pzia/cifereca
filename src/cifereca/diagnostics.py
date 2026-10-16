from __future__ import annotations
import json
import re
import threading
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from .diagnostic_task_context import numilog_task_reference

SENSITIVE_KEYS = re.compile(r"pass|password|cookie|token|secret|readerid|barcode|session|authorization", re.I)


def redact_text(text: str) -> str:
    # Delivery URLs can hide tokens in paths (PNB LOAN), fragments or queries.
    # Keep the origin only; never attempt to guess which query keys are safe.
    text = re.sub(r"https?://[^\s<>\"']+", lambda m: _redact_url(m.group()), text, flags=re.I)
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer <redacted>", text)
    text = re.sub(r"(?i)\b(password|cookie|token|secret|authorization|session|barcode|readerid)\b\s*[:=]\s*[^\s,;]+", r"\1=<redacted>", text)
    text = re.sub(r"(?i)([\"']?(?:password|cookie|token|secret|authorization|session|barcode|readerid)[\"']?\s*[:=]\s*)(?:[\"'][^\"']*[\"']|[^\s,;}]+)", r"\1<redacted>", text)
    return text


def _redact_url(url):
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(url)
        if parsed.query or parsed.fragment or parsed.username or re.search(r"(?i)/(?:LOAN|URLLink\.acsm)(?:/|$)", parsed.path):
            return f"{parsed.scheme}://{parsed.hostname}/<redacted>"
        return url
    except ValueError:
        return "<redacted-url>"


def redact(value: Any, key: str = "") -> Any:
    if SENSITIVE_KEYS.search(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, key) for v in value]
    if isinstance(value, str):
        text = redact_text(value)
        return text[:500] + "…" if len(text) > 500 else text
    return value


@dataclass
class DiagnosticEntry:
    timestamp: str
    category: str
    operation: str
    status: str
    duration_ms: int | None = None
    detail: dict[str, Any] | None = None
    task_ref: str | None = None


class Diagnostics:
    LEVELS = {"errors": 0, "normal": 1, "detailed": 2, "verbose": 2, "trace": 3}

    def __init__(self, enabled: bool = True, level: str = "normal", max_entries: int = 500):
        self.enabled = enabled
        self.level = level if level in self.LEVELS else "normal"
        self._items: deque[DiagnosticEntry] = deque(maxlen=max_entries)
        self._lock = threading.Lock()

    def add(self, category: str, operation: str, status: str = "ok", duration_ms: int | None = None,
            detail: dict[str, Any] | None = None, level: str = "normal") -> None:
        if not self.enabled:
            return
        if self.LEVELS.get(level, 1) > self.LEVELS.get(self.level, 1):
            return
        if self.level == "errors" and status == "ok":
            return
        entry = DiagnosticEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            category=redact_text(category),
            operation=redact_text(operation),
            status=status,
            duration_ms=duration_ms,
            detail=redact(detail or {}),
            task_ref=numilog_task_reference() if category == "NUMILOG" else None,
        )
        with self._lock:
            self._items.append(entry)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [asdict(x) for x in self._items]
            for row in rows:
                if row.get("task_ref") is None:
                    row.pop("task_ref", None)
            return rows

    def text(self, version: str, extra: dict[str, Any] | None = None) -> str:
        lines = [
            f"cifereca v{version}",
            f"Generated: {datetime.now(timezone.utc).isoformat(timespec='milliseconds')}",
            f"Diagnostics: {'enabled' if self.enabled else 'disabled'} level={self.level}",
        ]
        if extra:
            for k, v in redact(extra).items():
                lines.append(f"{k}: {v}")
        lines.append("")
        for item in self.snapshot():
            lines.append(f"[{item['timestamp']}] {item['category']} {item['operation']} status={item['status']}")
            if item.get("task_ref"):
                lines.append("task_ref=" + item["task_ref"])
            if item.get("duration_ms") is not None:
                lines.append(f"duration={item['duration_ms']}ms")
            detail = item.get("detail") or {}
            if detail:
                for k, v in detail.items():
                    lines.append(f"{k}={json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
