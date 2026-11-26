#!/usr/bin/env python3
"""Synthetic-only GTK evidence. Never generates a human review verdict."""
import argparse
import hashlib
from io import BytesIO
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile

import bnf_private_review as bnf
import notice_c_private_review as generic
from notice_c_private_capture import CaptureError, MATRIX, ROOT, clean_head
from notice_c_private_final_receipt import native_aba_report


class MatrixFailure(CaptureError):
    """Only internal fixed matrix labels may be exposed."""


def pixel_digest(raw, expected_sha):
    """Hash decoded RGB samples, never PNG metadata or compression."""
    from PIL import Image
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise CaptureError("capture changed after validation")
    try:
        with Image.open(BytesIO(raw)) as image:
            image.load()
            return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise CaptureError("invalid decoded capture") from exc


def capture_pixel_digest(root, row, family):
    mode, width, stage = row["appearance"], row["width"], row["stage"]
    if mode not in ("light", "dark") or width not in ("1366", "1600"):
        raise CaptureError("invalid capture identity")
    if family == "generic" and stage in generic.STAGES:
        path = root / mode / f"book-detail-{width}" / (stage + ".png")
        raw = generic.read_regular(path, 16 * 1024 * 1024)
    elif family == "bnf" and stage in bnf.STAGES:
        path = bnf.capture_folder(root, mode, int(width)) / (stage + ".png")
        raw = bnf.read_regular(path, 16 * 1024 * 1024)
    else:
        raise CaptureError("invalid capture stage")
    return pixel_digest(raw, row["sha256"])


def verify_theme_pairs(rows, root, family):
    """Reject identical light/dark pixels for the same stage and geometry."""
    pairs = {}
    for row in rows:
        key = (row["width"], row["stage"])
        mode = row["appearance"]
        file_digest = row["sha256"]
        if mode not in ("light", "dark") or not re.fullmatch("[0-9a-f]{64}", file_digest):
            raise CaptureError("invalid capture identity")
        pair = pairs.setdefault(key, {})
        if mode in pair:
            raise CaptureError("duplicate capture identity")
        pair[mode] = capture_pixel_digest(root, row, family)
    if not pairs or any(set(pair) != {"light", "dark"} or
                        pair["light"] == pair["dark"] for pair in pairs.values()):
        raise CaptureError("missing or identical theme pair")


def route_title_keys():
    return tuple(f"synthetic_title_00-notice-c-{route}-{variant}-{phase}"
                 for phase in ("cold", "offline") for route in generic.ROUTES
                 for variant in ("list", "detail"))


def verify_native_titles(root, sha):
    for theme, width in MATRIX:
        for folder, reader, keys in (
            (root / "generic" / theme / f"book-detail-{width}", generic.read_regular,
             ("synthetic_native_title_readable", *route_title_keys())),
            (bnf.capture_folder(root / "bnf", theme, width), bnf.read_regular,
             ("synthetic_native_title_0", "synthetic_native_title_1")),
        ):
            report = json.loads(reader(folder / "screen.json", 8 * 1024 * 1024))
            if (type(report) is not dict or report.get("git_sha") != sha
                    or report.get("status") != "PASS"
                    or type(report.get("checks")) is not dict
                    or not all(report["checks"].get(key) is True for key in keys)):
                raise CaptureError("missing native title witness")


def verify(root, sha):
    first, gf = generic.verify_captures(root / "generic", sha)
    second, bf = bnf.inventory(root / "bnf", sha)
    for theme, width in MATRIX:
        native_aba_report(root / "generic" / theme /
                          f"book-detail-{width}" / "screen.json", sha=sha)
    if len(first) != 128 or len(second) != 96 or clean_head() != sha:
        raise CaptureError("incomplete synthetic matrix")
    if not all(type(value) is str and re.fullmatch("[0-9a-f]{64}", value)
               for value in (gf, bf)):
        raise CaptureError("invalid matrix fingerprint")
    verify_native_titles(root, sha)
    verify_theme_pairs(first, root / "generic", "generic")
    verify_theme_pairs(second, root / "bnf", "bnf")
    return dict(status="SYNTHETIC_GTK_MATRIX_VERIFIED", git_sha=sha,
                capture_count=224, native_aba_surfaces_per_configuration=12,
                generic_fingerprint=gf, bnf_fingerprint=bf,
                human_visual_review=False)


def run(python, expected_sha):
    sha = clean_head()
    if sha != expected_sha:
        raise CaptureError("wrong expected checkout")
    old = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="synthetic-4980-") as tmp:
            root = Path(tmp)
            for theme, width in MATRIX:
                for family, scenario, extra, target in (
                    ("generic", f"book-detail-{width}", ["--synthetic-matrix"],
                     root / "generic" / theme),
                    ("bnf", "bnf-covers-1366", ["--bnf-width", str(width), "--synthetic-matrix"],
                     root / "bnf" / theme / str(width)),
                ):
                    target.mkdir(parents=True, exist_ok=True)
                    if clean_head() != sha:
                        raise CaptureError("checkout changed")
                    result = subprocess.run(
                        [python, str(ROOT / "e2e/run.py"), "--scenario", scenario,
                         "--appearance", theme, "--out", str(target),
                         "--python", python, *extra],
                        cwd=ROOT, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=180, check=False)
                    if result.returncode:
                        raise MatrixFailure(f"{family}_{theme}_{width}")
            return verify(root, sha)
    finally:
        os.umask(old)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.python, args.expected_sha)))
    except MatrixFailure as exc:
        allowed = {f"{family}_{theme}_{width}" for theme, width in MATRIX
                   for family in ("generic", "bnf")}
        code = str(exc) if str(exc) in allowed else "unknown"
        print(json.dumps(dict(status="SYNTHETIC_GTK_MATRIX_REFUSED", failed_case=code)))
        return 1
    except (CaptureError, generic.ReviewError, OSError, ValueError,
            subprocess.SubprocessError):
        print('{"status":"SYNTHETIC_GTK_MATRIX_REFUSED"}')
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
