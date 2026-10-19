#!/usr/bin/env python3
"""Offline UX review of real GTK Synchronisation first-fold measurements (#2141).

Only fixed-schema aggregate numbers leave this process. Never print raw
screen.json, its filename, exception messages, account names or screenshots.
This is NOT a pass/fail GTK test or a CI artifact publisher.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

MAX_INPUT_BYTES = 2_000_000
COUNTERS = (
    "source_cards", "source_status_rows", "detail_rows", "source_summary_lines",
    "action_buttons", "primary_source_actions", "visible_source_status_rows",
    "visible_primary_source_actions", "visible_action_buttons",
)
DIMENSIONS = ("window_width", "window_height", "viewport_width", "viewport_height")
FLOATS = ("scroll_content_height", "scroll_extent_viewports")
BOOLEANS = ("global_action_visible",)


def _read_safe_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_INPUT_BYTES:
        raise ValueError("invalid evidence")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if type(payload) is not dict:
        raise ValueError("invalid evidence")
    return payload


def _metric(value: dict) -> dict:
    if type(value) is not dict:
        raise ValueError("invalid measurements")
    safe = {}
    for key in (*COUNTERS, *DIMENSIONS):
        item = value.get(key)
        if type(item) is not int or item < 0 or item > 100_000:
            raise ValueError("invalid measurements")
        safe[key] = item
    for key in FLOATS:
        item = value.get(key)
        if type(item) not in (int, float) or not math.isfinite(item) or not 0 <= item <= 1_000_000:
            raise ValueError("invalid measurements")
        safe[key] = round(float(item), 2)
    for key in BOOLEANS:
        if type(value.get(key)) is not bool:
            raise ValueError("invalid measurements")
        safe[key] = value[key]
    for visible, total in (
        ("visible_source_status_rows", "source_status_rows"),
        ("visible_primary_source_actions", "primary_source_actions"),
        ("visible_action_buttons", "action_buttons"),
    ):
        if safe[visible] > safe[total]:
            raise ValueError("inconsistent measurements")
    safe["sources_visible_percent"] = (
        round(100 * safe["visible_source_status_rows"] / safe["source_status_rows"], 1)
        if safe["source_status_rows"] else None
    )
    safe["primary_actions_visible_percent"] = (
        round(100 * safe["visible_primary_source_actions"] / safe["primary_source_actions"], 1)
        if safe["primary_source_actions"] else None
    )
    return safe


def review(path: Path) -> dict:
    data = _read_safe_json(path)
    if data.get("status") != "PASS" or data.get("scope") != (
        "real MainWindow and real Diagnostics page contribution, simulated brokers"
    ):
        raise ValueError("unverified GTK evidence")
    samples = {"baseline": _metric(data.get("sync_first_fold_ux"))}
    resized = data.get("sync_1280_first_fold_ux")
    if resized is not None:
        if type(resized) is not dict:
            raise ValueError("invalid measurements")
        for key in ("normal_font", "large_font"):
            if key in resized:
                sample = _metric(resized[key])
                # The requested width may be restricted by window minimums.
                # Report actual allocation; never silently call it 1280px.
                sample["target_width_reached"] = 1240 <= sample["window_width"] <= 1300
                samples["1280_" + key] = sample
    return {"schema": "cifereca-ux-first-fold/v1", "samples": samples,
            "interpretation": "geometry only; legibility and user acceptance not certified"}



def compare(before: dict, after: dict) -> dict:
    """Explain when two private GTK samples support a geometric comparison.

    Increases in first-screen intersections are *not* accessibility or
    legibility verdicts. All labels/reasons are fixed, never read from input.
    """
    observations = {}
    before_samples, after_samples = before["samples"], after["samples"]
    for name in sorted(set(before_samples) | set(after_samples)):
        previous, current = before_samples.get(name), after_samples.get(name)
        if previous is None or current is None:
            observations[name] = {"comparable": False, "reason": "missing_sample"}
            continue
        if (abs(previous["window_width"] - current["window_width"]) > 24
                or abs(previous["window_height"] - current["window_height"]) > 48):
            observations[name] = {"comparable": False, "reason": "different_window_geometry"}
            continue
        if (previous["source_cards"] != current["source_cards"]
                or previous["source_status_rows"] != current["source_status_rows"]
                or previous["primary_source_actions"] != current["primary_source_actions"]):
            observations[name] = {"comparable": False, "reason": "different_source_population"}
            continue

        def difference(key: str):
            before_value, after_value = previous[key], current[key]
            return round(after_value - before_value, 2) if (
                before_value is not None and after_value is not None
            ) else None

        observations[name] = {
            "comparable": True,
            "source_visibility_points_delta": difference("sources_visible_percent"),
            "primary_action_visibility_points_delta": difference(
                "primary_actions_visible_percent"
            ),
            "first_screen_source_intersections_delta": difference(
                "visible_source_status_rows"
            ),
            "first_screen_primary_action_intersections_delta": difference(
                "visible_primary_source_actions"
            ),
            "scroll_viewports_delta": difference("scroll_extent_viewports"),
            "global_action_visible_before": previous["global_action_visible"],
            "global_action_visible_after": current["global_action_visible"],
        }
    return {
        "schema": "cifereca-ux-first-fold-comparison/v1",
        "samples": observations,
        "interpretation": "geometry differences only; no human-legibility verdict",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="private local E2E screen.json")
    parser.add_argument("--before", type=Path, help="optional earlier private E2E report")
    args = parser.parse_args(argv)
    try:
        result = {"after": review(args.report)}
        if args.before is not None:
            result["before"] = review(args.before)
            result["comparison"] = compare(result["before"], result["after"])
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
        print("UX measurement refused: invalid or unverified private report", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
