#!/usr/bin/env python3
"""Publish only bounded, allowlisted E2E verdicts, never raw GTK evidence (#1143)."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 1024 * 1024
MAX_ROWS = 128


def _allowed_scenarios() -> set[str]:
    # These are reviewed scenario identifiers from source, not user-supplied
    # test output. Never trust a scenario string from summary.json alone.
    spec = importlib.util.spec_from_file_location("cifereca_e2e_export_scenarios", ROOT / "e2e/run.py")
    if spec is None or spec.loader is None:
        raise ValueError("E2E scenario source unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.SCENARIOS)


def safe_summary(source: Path) -> dict:
    # Fail closed for symlinks, invalid JSON, unreasonable sizes or fields.
    if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_BYTES:
        raise ValueError("invalid E2E input")
    data = json.loads(source.read_bytes().decode("utf-8"))
    if not isinstance(data, list) or len(data) > MAX_ROWS:
        raise ValueError("invalid E2E result list")
    allowed = _allowed_scenarios()
    rows = []
    for item in data:
        if not isinstance(item, dict) or item.get("scenario") not in allowed:
            raise ValueError("unexpected E2E scenario")
        code = item.get("returncode")
        if type(code) is not int or not -255 <= code <= 255:
            raise ValueError("invalid E2E return code")
        row = {
            "scenario": item["scenario"],
            "status": "PASS" if code == 0 else "FAIL",
            "returncode": code,
        }
        ratio = item.get("visual_change_ratio")
        if ratio is not None:
            if type(ratio) not in (int, float) or not math.isfinite(ratio) or not 0 <= ratio <= 1:
                raise ValueError("invalid E2E visual ratio")
            row["visual_change_ratio"] = round(ratio, 6)
        # Deliberately discard every other field, including raw assertion
        # messages, personal paths, URLs, JSON/JSONL, logs, pixels and images.
        rows.append(row)
    return {"schema": "cifereca-e2e-public-verdict/v1", "results": rows}


def export(source: Path, output_directory: Path) -> Path:
    payload = safe_summary(source)
    # Exclusive creation avoids reusing an attacker-controlled or stale file.
    output_directory.mkdir(parents=True, exist_ok=False)
    target = output_directory / "summary.json"
    with target.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False)
        handle.write("\n")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export privacy-safe E2E verdicts only")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        export(args.source, args.out_dir)
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        # Never echo arbitrary exception text: it can include untrusted data.
        print("E2E publication refused (invalid or untrusted evidence)", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
