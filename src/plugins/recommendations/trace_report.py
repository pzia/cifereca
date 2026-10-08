from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
import statistics
import sys


TRACE_MARKERS = (
    "TRACE recommendations performance ",
    "TRACE recommendations ui ",
    "TRACE recommendations library-snapshot ",
)


@dataclass(frozen=True)
class TraceRecord:
    kind: str
    values: dict[str, object]


def _coerce(value: str) -> object:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def parse_trace_lines(lines: Iterable[str]) -> list[TraceRecord]:
    records: list[TraceRecord] = []
    for raw in lines:
        line = str(raw).strip()
        marker = next((candidate for candidate in TRACE_MARKERS if candidate in line), None)
        if marker is None:
            continue
        payload = line.split(marker, 1)[1].strip()
        kind = marker.removeprefix("TRACE recommendations ").strip()
        values: dict[str, object] = {}
        for token in payload.split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            if key:
                values[key] = _coerce(value)
        records.append(TraceRecord(kind=kind, values=values))
    return records


def _numbers(records: Iterable[TraceRecord], key: str) -> list[float]:
    out: list[float] = []
    for record in records:
        value = record.values.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out.append(float(value))
    return out


def _format_stats(label: str, values: list[float]) -> str | None:
    if not values:
        return None
    return (
        f"{label}: médiane={statistics.median(values):.3f} ms "
        f"min={min(values):.3f} max={max(values):.3f}"
    )


def build_report(records: Iterable[TraceRecord]) -> str:
    rows = list(records)
    performance = [row for row in rows if row.kind == "performance"]
    ui = [row for row in rows if row.kind == "ui"]
    snapshots = [row for row in rows if row.kind == "library-snapshot"]

    cached = sum(row.values.get("snapshot_cached") is True for row in performance)
    uncached = sum(row.values.get("snapshot_cached") is False for row in performance)

    lines = [
        f"refreshes={len(performance)} ui={len(ui)} snapshots={len(snapshots)}",
        f"snapshot_cache: cached={cached} uncached={uncached}",
    ]
    for label, key, source in (
        ("snapshot", "snapshot_ms", performance),
        ("profil", "profile_ms", performance),
        ("catalogues", "providers_ms", performance),
        ("recherche", "catalog_ms", performance),
        ("classement", "rank_ms", performance),
        ("total calcul", "total_ms", performance),
        ("application UI", "apply_ms", ui),
        ("total UI", "total_ui_ms", ui),
    ):
        formatted = _format_stats(label, _numbers(source, key))
        if formatted:
            lines.append(formatted)

    uncached_snapshot = [
        float(row.values["snapshot_ms"])
        for row in performance
        if row.values.get("snapshot_cached") is False
        and isinstance(row.values.get("snapshot_ms"), (int, float))
    ]
    cached_snapshot = [
        float(row.values["snapshot_ms"])
        for row in performance
        if row.values.get("snapshot_cached") is True
        and isinstance(row.values.get("snapshot_ms"), (int, float))
    ]
    if uncached_snapshot and cached_snapshot:
        lines.append(
            "snapshot chaud/froid: "
            f"{statistics.median(uncached_snapshot):.3f} ms → "
            f"{statistics.median(cached_snapshot):.3f} ms"
        )

    if len(performance) >= 3 and uncached > 1:
        lines.append(
            "ATTENTION: plusieurs snapshots non cachés sur des relances successives; "
            "vérifier les notifications identity_overview."
        )
    elif len(performance) >= 3 and uncached <= 1:
        lines.append("OK: après l'amorçage, les relances observées réutilisent le snapshot.")

    return "\n".join(lines)


def _read_lines(path: str) -> Iterable[str]:
    if path == "-":
        return sys.stdin
    return Path(path).read_text(encoding="utf-8", errors="replace").splitlines()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Résume les traces de performance expurgées de Recommendations."
    )
    parser.add_argument(
        "log",
        nargs="?",
        default="-",
        help="fichier de log à analyser, ou - pour stdin",
    )
    args = parser.parse_args(argv)
    records = parse_trace_lines(_read_lines(args.log))
    if not records:
        print("Aucune trace Recommendations reconnue.", file=sys.stderr)
        return 2
    print(build_report(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
