#!/usr/bin/env python3
"""Launch a separate GTK application, send optional X11 input, and save its screen."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback

from PIL import Image, ImageGrab
from x11_input import X11Input


def coordinate(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"([0-9]+),([0-9]+)", value)
    if not match:
        raise argparse.ArgumentTypeError("use X,Y pixel coordinates, for example 400,185")
    return tuple(map(int, match.groups()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait", type=float, default=0.0, help="legacy extra delay after visibility detection")
    parser.add_argument("--startup-timeout", type=float, default=10.0, help="maximum seconds to wait for a non-blank application surface")
    parser.add_argument("--out", type=Path, default=Path("evidence/application.png"), help="PNG screenshot path")
    parser.add_argument("--click", action="append", default=[], type=coordinate, metavar="X,Y", help="inject a mouse click (repeatable)")
    parser.add_argument("--type-text", help="inject keyboard text after the clicks; omitted from the report")
    parser.add_argument("--key", action="append", default=[], help="inject an X11 keysym such as Return (repeatable)")
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="application working directory")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="application command after --")
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide the application command after --")
    if args.wait < 0 or args.wait > 60:
        parser.error("--wait must be between 0 and 60 seconds")
    if args.startup_timeout <= 0 or args.startup_timeout > 60:
        parser.error("--startup-timeout must be in ]0,60] seconds")
    if not os.environ.get("DISPLAY") or os.environ.get("GDK_BACKEND") != "x11":
        parser.error("run capture-app under the Xvfb launcher: ./headless -- ./capture-app ...")

    output = args.out.resolve()
    report_path = output.with_suffix(".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "result": "FAIL",
        "backend": "GDK X11 / Xvfb",
        "display": os.environ["DISPLAY"],
        "screen": os.environ.get("CODEX_HEADLESS_SCREEN", "1060x700x24"),
        "application": Path(command[0]).name,
        "working_directory": str(args.cwd.resolve()),
        "actions": [],
        "screenshot": None,
    }
    process: subprocess.Popen | None = None
    device: X11Input | None = None
    return_code = 1
    try:
        process = subprocess.Popen(command, cwd=args.cwd.resolve(), env=os.environ.copy())
        report["pid"] = process.pid
        deadline = time.monotonic() + args.startup_timeout
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"GTK application exited during startup (exit {process.returncode}).")
            probe = ImageGrab.grab(xdisplay=os.environ["DISPLAY"]).convert("RGB")
            colors = probe.getcolors(maxcolors=2)
            if colors is None or len(colors) > 1:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("GTK application did not expose a non-blank surface before timeout.")
            time.sleep(0.025)
        if args.wait:
            time.sleep(args.wait)

        if args.click or args.type_text or args.key:
            device = X11Input()
        for x, y in args.click:
            device.click(x, y)
            report["actions"].append({"type": "click", "x": x, "y": y})
            time.sleep(0.15)
        if args.type_text:
            if not args.type_text.isascii() or not args.type_text.isprintable():
                raise ValueError("--type-text currently accepts printable ASCII text only")
            device.type_text(args.type_text)
            report["actions"].append({"type": "keyboard_text", "character_count": len(args.type_text)})
            time.sleep(0.2)
        for key in args.key:
            device.key(key, True)
            device.key(key, False)
            report["actions"].append({"type": "key", "keysym": key})
            time.sleep(0.1)

        if process.poll() is not None:
            raise RuntimeError(f"GTK application closed before capture (exit {process.returncode}).")
        image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        image.save(output, format="PNG")
        with Image.open(output) as image:
            image.load()
            extrema = image.convert("RGB").getextrema()
            colors = image.convert("RGB").getcolors(maxcolors=2)
            report["screenshot"] = {
                "file": str(output),
                "width": image.width,
                "height": image.height,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "nonblank": colors is None or len(colors) > 1,
            }
        if not report["screenshot"]["nonblank"]:
            raise RuntimeError("The captured screen was blank.")
        report["application_still_open_at_capture"] = process.poll() is None
        report["result"] = "PASS"
        return_code = 0
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        traceback.print_exc()
    finally:
        if device:
            device.close()
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    print(f"{report['result']}: {report['screenshot'] or report.get('error')}")
    print(f"Rapport : {report_path}")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
