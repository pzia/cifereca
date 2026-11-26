#!/usr/bin/env python3
"""Private #4980 BnF visual evidence, bound to a clean exact checkout.

Uses the existing native probe and local capture orchestration conventions.
No real provider requests, PNG upload, or automatic visual verdict.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from notice_c_private_capture import CaptureError, MATRIX, ROOT, clean_head

STAGES = (
    "01-bnf-search-two-covers", "02-bnf-first-detail", "03-bnf-second-detail",
    "04b-bnf-search-exact-detail-cover-in-list",
    "04c-bnf-search-late-cover-survives-return",
    "04-bnf-missing-cover-placeholder", "05-bnf-loan-list-two-covers",
    "06-bnf-first-loan-detail", "07-bnf-second-loan-detail",
    "08-bnf-exact-remote-loan-cover", "09-bnf-embedded-over-remote-loan-cover",
    "10-bnf-embedded-over-valid-supplier-cover",
    "10b-bnf-embedded-conflict-placeholder", "10c-bnf-embedded-conflict-recovered",
    "11-bnf-late-library-loan-list-and-detail", "11b-bnf-two-exact-loans-reopened",
) + tuple(
    f"12-c-{route}-{variant}-{phase}"
    for phase in ("cold", "offline") for route in ("loans", "history")
    for variant in ("list", "detail")
)
CHECKS = (
    "two_real_gtk_search_images", "first_real_gtk_detail_cover",
    "second_real_gtk_detail_cover", "search_detail_cover_rebinds_same_gtk_row",
    "search_exact_cover_survives_reselection_gtk",
    "real_bnf_provider_to_scoped_loan_projection", "two_real_gtk_loan_list_images",
    "first_real_gtk_loan_detail_cover", "second_real_gtk_loan_detail_cover",
    "loan_n2_n3_and_both_gtk_paints_observed", "missing_cover_no_image_get",
    "cache_shared_search_detail", "cache_shared_loans", "no_external_bnf_provider_io",
    "missing_loan_cover_no_image_get", "no_credential_or_token_network",
    "remote_entitlement_exact_loan_cover_in_gtk",
    "embedded_local_over_remote_in_real_gtk",
    "embedded_local_over_valid_supplier_in_real_gtk",
    "P0_4980_embedded_ambiguous_placeholder_real_gtk",
    "late_library_repaints_real_gtk_loan_list",
    "P1_3104_two_exact_loans_reselection_real_gtk",
    "P0_6366_mainwindow_borrower_views_restart_offline",
    "borrower_advisory_native_read_only", "borrower_advisory_native_stale_callback",
)
FIELDS = ("appearance", "width", "stage", "sha256", "verdict", "notes")


def local_only():
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        raise CaptureError("private BnF evidence is local only")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def external(path):
    # Reject a path inside the checkout, including through an alias.
    if path.resolve().is_relative_to(ROOT.resolve()):
        raise CaptureError("private evidence must be outside the checkout")
    return path.absolute()


def directory_fd(path):
    # Traverse every component relative to the already-open parent; do not
    # follow symlinks. Linux/X11 local harness, not a public CI exporter.
    path = Path(path).absolute()
    if ".." in path.parts:
        raise CaptureError("unsafe private directory")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_regular(path, limit):
    parent = directory_fd(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_size > limit):
                raise CaptureError("unsafe private evidence file")
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if (len(data) != before.st_size or len(data) > limit
                    or (before.st_size, before.st_mtime_ns, before.st_ctime_ns,
                        before.st_nlink) !=
                       (after.st_size, after.st_mtime_ns, after.st_ctime_ns,
                        after.st_nlink)):
                raise CaptureError("private evidence changed while reading")
            return data
    finally:
        os.close(parent)


def capture_folder(root, theme, width):
    return root / theme / str(width) / "bnf-covers-1366"


def validate_folder(folder, sha, theme, width):
    from PIL import Image

    size = (width, 1000 if width == 1600 else 900)
    raw = read_regular(folder / "screen.json", 8 * 1024 * 1024)
    report = json.loads(raw)
    if (type(report) is not dict or report.get("status") != "PASS"
            or report.get("git_sha") != sha
            or report.get("scenario") != f"bnf-covers-{width}"
            or report.get("backend") !=
                "GTK4/X11, BnF HTML parser + provider adapter, HTTPX MockTransport"
            or type(report.get("width")) is not int or report["width"] != size[0]
            or type(report.get("height")) is not int or report["height"] != size[1]
            or report.get("appearance") != theme):
        raise CaptureError("wrong BnF HEAD, geometry, appearance or verdict")
    checks = report.get("checks")
    if type(checks) is not dict or not all(checks.get(key) is True for key in CHECKS):
        raise CaptureError("missing native BnF assertion")
    for phase in ("cold", "offline"):
        receipt = report.get("notice_c_borrower_views_" + phase)
        if (type(receipt) is not dict
                or any(type(receipt.get(k)) is not int or receipt[k] != v
                       for k, v in (("views", 4), ("sqlite_reads", 1), ("image_gets_total", 1)))
                or type(receipt.get("gtk_applied")) is not int
                or receipt["gtk_applied"] < 4):
            raise CaptureError("missing exact borrower-view receipt")
    steps = report.get("snapshots")
    if (type(steps) is not list or len(steps) != len(STAGES)
            or any(type(step) is not dict or type(step.get("stage")) is not str
                   for step in steps)
            or {step["stage"] for step in steps} != set(STAGES)):
        raise CaptureError("incomplete or duplicate BnF capture inventory")
    by_stage = {step["stage"]: step for step in steps}
    rows = []
    for stage in STAGES:
        step = by_stage[stage]
        name = stage + ".png"
        if step.get("file") != name or step.get("appearance") != theme:
            raise CaptureError("wrong BnF capture name or appearance")
        raw_png = read_regular(folder / name, 16 * 1024 * 1024)
        sha_png = digest(raw_png)
        if step.get("sha256") != sha_png:
            raise CaptureError("BnF capture digest mismatch")
        with Image.open(BytesIO(raw_png)) as image:
            if image.format != "PNG" or image.size != size:
                raise CaptureError("wrong BnF PNG dimensions")
            image.verify()
        with Image.open(BytesIO(raw_png)) as image:
            image.load()
            if all(lo == hi for lo, hi in image.convert("RGB").getextrema()):
                raise CaptureError("uniform BnF screenshot")
        rows.append(dict(appearance=theme, width=str(width), stage=stage,
                         sha256=sha_png, verdict="", notes=""))
    return rows, digest(raw)


def inventory(root, sha):
    if type(sha) is not str or not re.fullmatch("[0-9a-f]{40}", sha):
        raise CaptureError("invalid expected HEAD")
    rows, hashes = [], []
    for theme, width in MATRIX:
        current, report_hash = validate_folder(capture_folder(root, theme, width),
                                               sha, theme, width)
        rows.extend(current)
        hashes.append(f"{theme}:{width}:report:{report_hash}")
        hashes.extend(f"{theme}:{width}:{row['stage']}:{row['sha256']}" for row in current)
    return rows, digest("\n".join(hashes).encode("ascii"))


def capture(root, *, python, sha):
    local_only()
    root = external(root)
    parent = directory_fd(root.parent)
    old_umask = os.umask(0o077)
    try:
        os.mkdir(root.name, mode=0o700, dir_fd=parent)
        for theme, width in MATRIX:
            if clean_head() != sha:
                raise CaptureError("checkout changed during BnF capture")
            target = root / theme / str(width)
            target.mkdir(mode=0o700, parents=True, exist_ok=False)
            run = subprocess.run(
                [python, str(ROOT / "e2e/run.py"), "--scenario", "bnf-covers-1366",
                 "--bnf-width", str(width), "--appearance", theme,
                 "--out", str(target), "--python", python],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False, timeout=180,
            )
            if run.returncode != 0 or clean_head() != sha:
                raise CaptureError("BnF native execution failed or checkout changed")
            validate_folder(capture_folder(root, theme, width), sha, theme, width)
        return {"status": "BNF_CAPTURES_READY_FOR_PRIVATE_REVIEW", "git_sha": sha,
                "scenario_count": len(MATRIX)}
    finally:
        os.umask(old_umask)
        os.close(parent)


def prepare(root, output, sha):
    local_only()
    rows, fingerprint = inventory(external(root), sha)
    output = external(output)
    parent = directory_fd(output.parent)
    try:
        os.mkdir(output.name, mode=0o700, dir_fd=parent)
    finally:
        os.close(parent)
    folder = directory_fd(output)
    try:
        for name in ("manifest.json", "review.csv"):
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=folder)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                if name == "manifest.json":
                    json.dump(dict(git_sha=sha, count=len(rows), fingerprint=fingerprint), stream)
                else:
                    writer = csv.DictWriter(stream, fieldnames=FIELDS)
                    writer.writeheader()
                    writer.writerows(rows)
    finally:
        os.close(folder)
    return {"status": "BNF_REVIEW_REQUIRED", "capture_count": len(rows)}


def verify(root, output, sha):
    local_only()
    rows, fingerprint = inventory(external(root), sha)
    output = external(output)
    manifest = json.loads(read_regular(output / "manifest.json", 65536))
    review = csv.DictReader(StringIO(
        read_regular(output / "review.csv", 1024 * 1024).decode("utf-8")
    ))
    if tuple(review.fieldnames or ()) != FIELDS:
        raise CaptureError("wrong private BnF review columns")
    reviewed = list(review)
    if (manifest != dict(git_sha=sha, count=len(rows), fingerprint=fingerprint)
            or len(reviewed) != len(rows)):
        raise CaptureError("incomplete or changed private BnF review")
    for expected, actual in zip(rows, reviewed):
        if (set(actual) != set(FIELDS)
                or any(actual[k] != expected[k] for k in FIELDS[:4])
                or actual["verdict"] != "OK" or actual["notes"] is None):
            raise CaptureError("missing or non-OK independent BnF review")
    return {"status": "BNF_CHECKLIST_VERIFIED", "git_sha": sha,
            "capture_count": len(rows), "fingerprint": fingerprint}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "prepare", "verify"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    try:
        local_only()
        sha = clean_head()
        if args.action == "capture":
            result = capture(args.root, python=args.python, sha=sha)
        elif args.output is None:
            raise CaptureError("private review output required")
        else:
            result = (prepare if args.action == "prepare" else verify)(
                args.root, args.output, sha)
        if clean_head() != sha:
            raise CaptureError("checkout changed during review")
        print(json.dumps(result))
    except (CaptureError, OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        print('{"status":"BNF_REVIEW_REFUSED"}')
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
