#!/usr/bin/env python3
"""Review real GTK Diagnostics entry geometry locally without leaking screen.json.

This is exploratory evidence (P0 #2139), not a GTK test or CI gate.
Only fixed control IDs and validated numeric/boolean fields reach stdout.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

MAX_REPORT_SIZE = 2_000_000
CONTROL_IDS = (
    "system_heading", "journals_heading", "source_filter", "severity_filter",
    "period_filter", "module_filter", "search_filter", "select_errors",
    "select_all", "copy_selection", "ai_heading",
)
MEASUREMENTS = ("baseline", "requested_1280")
BOOLEAN_FIELDS = (
    "present", "allocated", "intersects_first_screen", "fully_in_first_screen",
    "horizontal_clipping",
)


def read_private_report(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("invalid private report")
    length = path.stat().st_size
    if not 0 < length <= MAX_REPORT_SIZE:
        raise ValueError("invalid private report")
    data = json.loads(path.read_text(encoding="utf-8"))
    if type(data) is not dict:
        raise ValueError("invalid private report")
    if (data.get("status") != "PASS" or data.get("backend") != "GTK4/X11"
            or data.get("plugin") != "ui.page.diagnostics"
            or data.get("broker") != "fixture bornée"):
        raise ValueError("unverified GTK fixture")
    return data


def count(value: object, *, positive: bool = False) -> int:
    if type(value) is not int or not (0 < value if positive else 0 <= value) or value > 100_000:
        raise ValueError("invalid geometry")
    return value


def decimal(value: object, *, minimum: float = 0, maximum: float = 1_000_000) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("invalid geometry")
    return round(float(value), 2)


def control_summary(raw: object) -> dict:
    if type(raw) is not dict or type(raw.get("present")) is not bool:
        raise ValueError("invalid control")
    if not raw["present"]:
        return {"present": False}
    if type(raw.get("allocated")) is not bool:
        raise ValueError("invalid control")
    if not raw["allocated"]:
        # GTK may find a widget before its first allocation. Do not count it
        # as visible even if a stale bounds record was written.
        return {"present": True, "allocated": False}
    for field in BOOLEAN_FIELDS:
        if type(raw.get(field)) is not bool:
            raise ValueError("invalid control")
    visible = raw["intersects_first_screen"]
    fully = raw["fully_in_first_screen"]
    if fully and not visible:
        raise ValueError("inconsistent control visibility")
    percentage = decimal(raw.get("vertical_visible_percent"), maximum=100)
    top = decimal(raw.get("top_offset_viewports"), minimum=-1000, maximum=1000)
    if not visible and percentage > 0:
        raise ValueError("inconsistent viewport intersection")
    if fully and percentage < 99.8:
        raise ValueError("inconsistent fully visible control")
    return {
        "present": True,
        "allocated": True,
        "intersects_first_screen": visible,
        "fully_in_first_screen": fully,
        "horizontal_clipping": raw["horizontal_clipping"],
        "vertical_visible_percent": percentage,
        "top_offset_viewports": top,
    }


def sample_summary(raw: object) -> dict:
    if type(raw) is not dict or type(raw.get("controls")) is not dict:
        raise ValueError("invalid observations")
    summary = {}
    for key in ("window_width", "window_height", "viewport_width", "viewport_height"):
        summary[key] = count(raw.get(key), positive=True)
    summary["vertical_scroll_extent_viewports"] = decimal(raw.get("vertical_scroll_extent_viewports"))
    controls = raw["controls"]
    summary["controls"] = {
        key: control_summary(controls.get(key)) for key in CONTROL_IDS
    }
    summary["journals_on_first_screen"] = summary["controls"]["journals_heading"].get(
        "fully_in_first_screen", False
    )
    summary["visible_journal_filters"] = sum(
        summary["controls"][key].get("fully_in_first_screen", False)
        for key in ("source_filter", "severity_filter", "period_filter", "module_filter", "search_filter")
    )
    summary["clipped_controls"] = [
        key for key in CONTROL_IDS if summary["controls"][key].get("horizontal_clipping", False)
    ]
    return summary


def review(path: Path) -> dict:
    raw = read_private_report(path).get("diagnostics_entry_ux")
    if type(raw) is not dict or "baseline" not in raw:
        raise ValueError("GTK observation not present")
    samples = {}
    for key in MEASUREMENTS:
        if key in raw:
            samples[key] = sample_summary(raw[key])
            if key == "requested_1280":
                samples[key]["target_width_reached"] = (
                    1240 <= samples[key]["window_width"] <= 1300
                )
    return {
        "schema": "cifereca-ux-diagnostics-entry/v1", "samples": samples,
        "interpretation": "geometry only; no legibility, keyboard or screen-reader verdict",
    }


def compare(before: dict, after: dict) -> dict:
    result = {}
    for key in MEASUREMENTS:
        previous, current = before["samples"].get(key), after["samples"].get(key)
        if previous is None or current is None:
            result[key] = {"comparable": False, "reason": "missing_sample"}
        elif (abs(previous["window_width"] - current["window_width"]) > 24
              or abs(previous["window_height"] - current["window_height"]) > 48
              or abs(previous["viewport_width"] - current["viewport_width"]) > 32
              or abs(previous["viewport_height"] - current["viewport_height"]) > 48):
            result[key] = {"comparable": False, "reason": "different_window_geometry"}
        else:
            transitions = {}
            for name in CONTROL_IDS:
                p, c = previous["controls"][name], current["controls"][name]
                transitions[name] = {
                    "present_before": p["present"], "present_after": c["present"],
                    "fully_visible_before": p.get("fully_in_first_screen", False),
                    "fully_visible_after": c.get("fully_in_first_screen", False),
                    "horizontally_clipped_before": p.get("horizontal_clipping", False),
                    "horizontally_clipped_after": c.get("horizontal_clipping", False),
                }
            result[key] = {
                "comparable": True,
                "journals_fully_visible_before": previous["journals_on_first_screen"],
                "journals_fully_visible_after": current["journals_on_first_screen"],
                "fully_visible_journal_filters_delta": (
                    current["visible_journal_filters"] - previous["visible_journal_filters"]
                ),
                "scroll_viewports_delta": round(
                    current["vertical_scroll_extent_viewports"] -
                    previous["vertical_scroll_extent_viewports"], 2
                ),
                "controls": transitions,
            }
    return {
        "schema": "cifereca-ux-diagnostics-entry-comparison/v1",
        "samples": result,
        "interpretation": "first-screen intersections only; not a usability verdict",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="local private E2E screen.json")
    parser.add_argument("--before", type=Path, help="optional earlier private E2E screen.json")
    args = parser.parse_args(argv)
    try:
        result = {"after": review(args.report)}
        if args.before is not None:
            result["before"] = review(args.before)
            result["comparison"] = compare(result["before"], result["after"])
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError, RecursionError):
        print("UX Diagnostics review refused: invalid or unverified evidence", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
