#!/usr/bin/env python3
"""Run the four local #4980 GTK captures without publishing private evidence."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MATRIX = tuple((theme, width) for theme in ("light", "dark")
               for width in (1366, 1600))


class CaptureError(ValueError):
    """Closed error message; do not print local paths or runner output."""


def clean_head():
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
    if not re.fullmatch("[0-9a-f]{40}", head):
        raise CaptureError("invalid checkout HEAD")
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
        cwd=ROOT, stderr=subprocess.DEVNULL,
    )
    if dirty:
        raise CaptureError("checkout is not clean")
    return head


def capture_matrix(target: Path, *, python: str):
    # This is a local UX protocol, never an Actions artifact producer.
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        raise CaptureError("private visual capture is local only")
    if target.is_symlink() or target.exists():
        raise CaptureError("capture directory must be new")
    parent = target.parent
    if parent.is_symlink() or not parent.is_dir():
        raise CaptureError("capture parent is unsafe")
    target = target.resolve()
    if target.is_relative_to(ROOT.resolve()):
        raise CaptureError("private capture root must be outside the checkout")
    sha = clean_head()
    previous_umask = os.umask(0o077)
    completed = 0
    try:
        target.mkdir(mode=0o700, exist_ok=False)
        for theme, width in MATRIX:
            if clean_head() != sha:
                raise CaptureError("checkout changed during capture")
            mode_dir = target / theme
            mode_dir.mkdir(mode=0o700, exist_ok=True)
            run = subprocess.run(
                [python, str(ROOT / "e2e/run.py"),
                 "--scenario", f"book-detail-{width}", "--appearance", theme,
                 "--out", str(mode_dir), "--python", python],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False, timeout=180,
            )
            if run.returncode != 0:
                raise CaptureError("native GTK capture failed")
            report_path = mode_dir / f"book-detail-{width}" / "screen.json"
            if (report_path.parent.is_symlink() or report_path.is_symlink()
                    or not report_path.is_file() or report_path.stat().st_nlink != 1
                    or report_path.stat().st_size > 8 * 1024 * 1024):
                raise CaptureError("missing or unsafe native report")
            try:
                report = json.loads(report_path.read_bytes())
            except (ValueError, UnicodeError) as exc:
                raise CaptureError("invalid native report") from exc
            if (type(report) is not dict or report.get("status") != "PASS"
                    or report.get("git_sha") != sha
                    or report.get("scenario") != "book-detail-gtk"
                    or report.get("backend") != "GTK4/X11/XTEST"
                    or type(report.get("width")) is not int or report["width"] != width
                    or report.get("local_c_stage") != "complete"):
                raise CaptureError("native receipt is incomplete or not at the pinned HEAD")
            steps = report.get("steps")
            if (type(steps) is not list or not steps
                    or any(type(step) is not dict or step.get("appearance") != theme
                           for step in steps)):
                raise CaptureError("observed native appearance mismatch")
            if clean_head() != sha:
                raise CaptureError("checkout changed during capture")
            completed += 1
        # Not a visual approval: the separate private review must inspect
        # the original PNGs and issue a SHA-bound completed checklist.
        return {"status": "CAPTURES_READY_FOR_PRIVATE_REVIEW",
                "git_sha": sha, "scenario_count": completed}
    finally:
        os.umask(previous_umask)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    try:
        print(json.dumps(capture_matrix(args.root, python=args.python)))
    except (CaptureError, OSError, ValueError, subprocess.SubprocessError):
        print('{"status":"CAPTURE_REFUSED"}')
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
