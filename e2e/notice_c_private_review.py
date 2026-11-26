#!/usr/bin/env python3
"""Private #4980 six-view GTK review receipt; never uploads PNGs or notes."""
from __future__ import annotations

import argparse
import csv
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SIZES = {1366: (1366, 900), 1600: (1600, 1000)}
ROUTES = ("library", "recent", "search", "loans", "history", "recommendations")
MODES = ("light", "dark")
STAGES = tuple(
    f"00-notice-c-{route}-{variant}-{phase}"
    for phase in ("cold", "offline") for route in ROUTES
    for variant in ("list", "detail")
) + (
    "00-notice-c-warm-n2-cold-n3-recovered",
    "00-notice-c-distinct-c-shared-n3",
    "00-notice-c-invalidated-n2-warm-n3",
    "00-notice-c-epub-priority-cold",
    "00-notice-c-epub-priority-warm",
    "00-notice-c-epub-qualified-fallback",
    "00-notice-c-catalogue-http-fallback",
)
CHECKS = (
    "P0_4980_same_c_six_physical_views_restart_offline",
    "P0_4980_warm_n2_cold_n3_real_gtk",
    "P0_4980_distinct_c_shared_n3_real_gtk",
    "P0_4980_invalidated_n2_warm_n3_real_gtk",
    "P0_4980_epub_priority_cold_real_gtk",
    "P0_4980_epub_priority_warm_real_gtk",
    "P0_4980_epub_qualified_fallback_real_gtk",
    "P0_4980_catalogue_http_fallback_real_gtk",
    "P0_4980_epub_interrupted_publish_recovery_real_gtk",
    "P0_6168_postcommit_rebind_real_gtk",
)
FIELDS = ("appearance", "width", "stage", "sha256", "verdict", "notes")


class ReviewError(ValueError):
    """Closed error; never print private report contents."""


def read_regular(path, limit):
    if (path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1
            or path.stat().st_size > limit):
        raise ReviewError("unsafe or missing private review input")
    return path.read_bytes()


def directory(path):
    if path.is_symlink() or not path.is_dir():
        raise ReviewError("unsafe or missing private capture directory")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_captures(root, expected_sha):
    """Verify 124 original PNGs; no automatic visual verdict."""
    from PIL import Image
    if type(expected_sha) is not str or not re.fullmatch("[0-9a-f]{40}", expected_sha):
        raise ReviewError("invalid expected HEAD")
    directory(root)
    rows, fingerprints = [], []
    for mode in MODES:
        directory(root / mode)
        for width, size in SIZES.items():
            folder = root / mode / f"book-detail-{width}"
            directory(folder)
            raw = read_regular(folder / "screen.json", 8 * 1024 * 1024)
            try:
                report = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise ReviewError("invalid capture report") from exc
            if (type(report) is not dict or report.get("status") != "PASS"
                    or report.get("scenario") != "book-detail-gtk"
                    or report.get("backend") != "GTK4/X11/XTEST"
                    or report.get("git_sha") != expected_sha
                    or type(report.get("width")) is not int or report["width"] != width
                    or report.get("local_c_stage") != "complete"):
                raise ReviewError("wrong HEAD, scenario, geometry or incomplete probe")
            checks = report.get("checks")
            if type(checks) is not dict or not all(checks.get(k) is True for k in CHECKS):
                raise ReviewError("missing native cover check")
            for phase, downloads in (("cold", 1), ("offline", 0)):
                shared = report.get("notice_c_shared_six_views_" + phase)
                metrics = report.get("notice_c_cover_metrics_" + phase)
                if (type(shared) is not dict or shared.get("routes") != list(ROUTES)
                        or type(shared.get("views")) is not int or shared["views"] != 12
                        or shared.get("same_document") is not True
                        or shared.get("same_epoch") is not True
                        or type(shared.get("image_gets_total")) is not int
                        or shared["image_gets_total"] != 1
                        or type(metrics) is not dict
                        or metrics.get("fixture") != "one_c_six_views_mock_transport"
                        or type(metrics.get("n3")) is not dict
                        or type(metrics["n3"].get("download_ok")) is not int
                        or metrics["n3"]["download_ok"] != downloads):
                    raise ReviewError("missing exact six-view receipt")
            steps = report.get("steps")
            if type(steps) is not list or any(type(e) is not dict for e in steps):
                raise ReviewError("invalid capture inventory")
            fingerprints.append(f"{mode}:{width}:report:{digest(raw)}")
            for stage in STAGES:
                found = [e for e in steps if e.get("stage") == stage]
                if len(found) != 1:
                    raise ReviewError("missing or duplicate capture")
                entry = found[0]
                filename = stage + ".png"
                if entry.get("file") != filename or entry.get("appearance") != mode:
                    raise ReviewError("capture filename or observed appearance mismatch")
                png = read_regular(folder / filename, 16 * 1024 * 1024)
                sha = digest(png)
                if entry.get("sha256") != sha:
                    raise ReviewError("capture digest mismatch")
                try:
                    with Image.open(BytesIO(png)) as image:
                        if image.format != "PNG" or image.size != size:
                            raise ReviewError("capture geometry or format mismatch")
                        image.verify()
                    with Image.open(BytesIO(png)) as image:
                        image.load()
                        if all(lo == hi for lo, hi in image.convert("RGB").getextrema()):
                            raise ReviewError("uniform capture")
                except (OSError, ValueError, Image.DecompressionBombError) as exc:
                    raise ReviewError("invalid capture image") from exc
                rows.append(dict(appearance=mode, width=str(width), stage=stage,
                                 sha256=sha, verdict="", notes=""))
                fingerprints.append(f"{mode}:{width}:{stage}:{sha}")
    return rows, digest("\n".join(fingerprints).encode("ascii"))


def build(root, output, sha):
    rows, fingerprint = verify_captures(root, sha)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, data in (
        ("manifest.json", json.dumps({"git_sha": sha, "fingerprint": fingerprint,
                                     "count": len(rows)}) + "\n"),
        ("review.csv", None),
    ):
        fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            if data is None:
                writer = csv.DictWriter(stream, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            else:
                stream.write(data)
    return len(rows)


def verify_review(root, output, sha):
    rows, fingerprint = verify_captures(root, sha)
    directory(output)
    try:
        manifest = json.loads(read_regular(output / "manifest.json", 65536))
        reviewed = list(csv.DictReader(
            read_regular(output / "review.csv", 1024 * 1024).decode("utf-8").splitlines()
        ))
    except (ValueError, UnicodeError) as exc:
        raise ReviewError("invalid private checklist") from exc
    if manifest != {"git_sha": sha, "fingerprint": fingerprint, "count": len(rows)}:
        raise ReviewError("capture or report changed after review")
    if len(reviewed) != len(rows):
        raise ReviewError("incomplete checklist")
    for expected, actual in zip(rows, reviewed):
        if (set(actual) != set(FIELDS)
                or any(actual[k] != expected[k] for k in FIELDS[:4])
                or actual["verdict"] != "OK"):
            raise ReviewError("missing, mismatched or non-OK visual review")
    # Only closed aggregate fields, never image/path/note/report.
    return {"status": "CHECKLIST_VERIFIED", "git_sha": sha,
            "capture_count": len(rows), "fingerprint": fingerprint}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    try:
        if args.verify:
            print(json.dumps(verify_review(args.root, args.output, sha)))
        else:
            print(json.dumps({"status": "REVIEW_REQUIRED",
                              "capture_count": build(args.root, args.output, sha)}))
    except (ReviewError, OSError, ValueError):
        print('{"status":"REVIEW_REFUSED"}')
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
