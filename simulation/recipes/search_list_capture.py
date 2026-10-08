#!/usr/bin/env python3
"""Scénario X11 ciblé sur la liste de la page Recherche."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from PIL import ImageGrab


def harness_path() -> Path:
    value = os.environ.get("CIFERECA_GTK_TEST_HARNESS")
    if value:
        return Path(value).expanduser().resolve()
    return (Path(__file__).resolve().parents[2] / "environnements" / "openai-chat-debian").resolve()


HARNESS = harness_path()
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from x11_input import X11Input


def patch_window_size(width: int, height: int) -> Path:
    config_home = Path(os.environ["XDG_CONFIG_HOME"])
    path = config_home / "cifereca" / "config.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    ui = data.setdefault("ui", {})
    ui["width"] = int(width)
    ui["height"] = int(height)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def capture(path: Path) -> list[int] | None:
    image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
    image.save(path)
    # Xvfb uses black outside an unmanaged toplevel. This bbox therefore lets
    # the recipe distinguish screen size from the actual GTK window extent.
    bbox = image.getbbox()
    return list(bbox) if bbox else None


def shortcut(control: X11Input, modifier: str, key: str) -> None:
    control.key(modifier, True)
    try:
        control.key(key, True)
        control.key(key, False)
    finally:
        control.key(modifier, False)


def scroll_down(control: X11Input, width: int, height: int, steps: int = 10) -> None:
    x = max(320, min(width - 80, int(width * 0.47)))
    y = max(260, min(height - 80, int(height * 0.62)))
    control.xtst.XTestFakeMotionEvent(control.display, -1, x, y, 0)
    for _ in range(steps):
        control.xtst.XTestFakeButtonEvent(control.display, 5, True, 0)
        control.xtst.XTestFakeButtonEvent(control.display, 5, False, 0)
    control.x11.XSync(control.display, False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--query", default="UXSTRESS")
    parser.add_argument("--window-width", type=int, required=True)
    parser.add_argument("--window-height", type=int, default=720)
    parser.add_argument("--startup-wait", type=float, default=4.0)
    parser.add_argument("--search-wait", type=float, default=3.0)
    parser.add_argument("--ui-ready-wait", type=float, default=2.0)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("une commande Cifereca est requise après --")

    if not (760 <= args.window_width <= 4096):
        parser.error("--window-width doit être compris entre 760 et 4096")
    if not (540 <= args.window_height <= 4096):
        parser.error("--window-height doit être compris entre 540 et 4096")

    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    config_path = patch_window_size(args.window_width, args.window_height)

    stdout = (out / "cifereca.stdout.log").open("w", encoding="utf-8")
    stderr = (out / "cifereca.stderr.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        command,
        cwd=args.cwd.resolve(),
        env=os.environ.copy(),
        stdout=stdout,
        stderr=stderr,
        text=True,
    )

    control = None
    started = time.monotonic()
    try:
        time.sleep(args.startup_wait)
        if proc.poll() is not None:
            raise RuntimeError(f"Cifereca arrêté avant interaction (rc={proc.returncode})")

        # The first cold GTK startup can present the window before deferred
        # page contributions are activated. Keep the interaction deterministic.
        time.sleep(args.ui_ready_wait)
        control = X11Input()

        # Raccourci canonique : ouvre Recherche si nécessaire et focalise le champ.
        shortcut(control, "Control_L", "f")
        time.sleep(0.5)
        control.select_all_delete()
        control.type_text(args.query)
        control.key("Return", True)
        control.key("Return", False)
        time.sleep(args.search_wait)

        first = out / "01-search-list.png"
        first_bbox = capture(first)

        # Select the first visible result. The master-detail contract is part of
        # this recipe: pathological row content must never make the detail pane
        # unreachable after selection.
        select_x = max(330, min(args.window_width - 120, int(args.window_width * 0.48)))
        select_y = 240
        control.click(select_x, select_y)
        time.sleep(1.5)
        selected = out / "02-selected-detail.png"
        selected_bbox = capture(selected)

        scroll_down(control, args.window_width, args.window_height, steps=12)
        time.sleep(1.0)
        second = out / "03-search-list-scrolled.png"
        second_bbox = capture(second)

        report = {
            "query": args.query,
            "window_requested": [args.window_width, args.window_height],
            "display": os.environ.get("DISPLAY"),
            "screen": os.environ.get("CODEX_HEADLESS_SCREEN"),
            "config": str(config_path),
            "captures": [str(first), str(selected), str(second)],
            "selection_click": [select_x, select_y],
            "capture_non_black_bbox": {
                "initial": first_bbox,
                "selected": selected_bbox,
                "scrolled": second_bbox,
            },
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "process_alive_after_capture": proc.poll() is None,
        }
        (out / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        if control is not None:
            try:
                shortcut(control, "Control_L", "q")
            except Exception:
                pass
            control.close()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        stdout.close()
        stderr.close()


if __name__ == "__main__":
    raise SystemExit(main())
