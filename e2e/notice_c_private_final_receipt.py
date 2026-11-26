#!/usr/bin/env python3
"""Reconcile both private #4980 review receipts on ONE clean exact HEAD.

Use only after the BnF capture producer and the six-view native late ABA
probe are integrated. The two independently completed checklists remain
local. This is a synthetic GTK evidence verifier, not an automated UX
approval or a real provider/profile receipt.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

import bnf_private_review as bnf
import notice_c_private_review as generic
from notice_c_private_capture import CaptureError, MATRIX, clean_head

ABA_MARKERS = (
    "P0_4980_six_view_native_paint_lease_aba_audit",
    "P0_4980_six_view_native_late_n3_aba_rejected",
)
ABA_COUNTS = ("notice_c_native_lease_audit_count",
              "notice_c_native_late_aba_count")


def require_receipt(receipt, *, status, sha, count):
    if (type(receipt) is not dict
            or set(receipt) != {"status", "git_sha", "capture_count", "fingerprint"}
            or receipt["status"] != status or receipt["git_sha"] != sha
            or type(receipt["capture_count"]) is not int
            or receipt["capture_count"] != count
            or type(receipt["fingerprint"]) is not str
            or not re.fullmatch("[0-9a-f]{64}", receipt["fingerprint"])):
        raise CaptureError("incomplete exact-HEAD private review receipt")


def native_aba_report(path, *, sha):
    raw = bnf.read_regular(path, 8 * 1024 * 1024)
    data = json.loads(raw)
    if (type(data) is not dict or data.get("status") != "PASS"
            or data.get("git_sha") != sha
            or data.get("local_c_stage") != "complete"):
        raise CaptureError("wrong native ABA report")
    checks = data.get("checks")
    if (type(checks) is not dict
            or not all(checks.get(key) is True for key in ABA_MARKERS)
            or any(type(data.get(key)) is not int or data[key] != 12
                   for key in ABA_COUNTS)):
        raise CaptureError("incomplete native ABA surface coverage")
    metrics = data.get("notice_c_cover_metrics_offline")
    if type(metrics) is not dict or type(metrics.get("n4")) is not dict:
        raise CaptureError("missing native ABA counters")
    n4 = metrics["n4"]
    if (type(n4.get("ui_stale")) is not int or n4["ui_stale"] < 12
            or type(n4.get("ui_failed")) is not int or n4["ui_failed"] != 0
            or type(n4.get("ui_applied")) is not int or n4["ui_applied"] < 24):
        raise CaptureError("invalid native ABA counters")
    return bnf.digest(raw)


def verify_matrix(generic_root, generic_review, bnf_root, bnf_review):
    bnf.local_only()
    sha = clean_head()
    paths = tuple(bnf.external(path) for path in (
        generic_root, generic_review, bnf_root, bnf_review,
    ))
    for path in paths:
        # Use nofollow traversal for each complete private root, not just
        # its basename. The existing individual validators still own the
        # stage inventory and per-image integrity checks.
        fd = bnf.directory_fd(path)
        os.close(fd)
    for i, left in enumerate(paths):
        for right in paths[i + 1:]:
            if left.is_relative_to(right) or right.is_relative_to(left):
                raise CaptureError("private evidence roots must be distinct")
    generic_root, generic_review, bnf_root, bnf_review = paths

    def aba_digests():
        return tuple(native_aba_report(
            generic_root / theme / f"book-detail-{width}" / "screen.json",
            sha=sha,
        ) for theme, width in MATRIX)

    # Check ABA at the same HEAD before and after both existing validators.
    # The generic fingerprint incorporates the native report bytes and
    # each PNG; the BnF fingerprint independently covers its native reports.
    before = aba_digests()
    first = generic.verify_review(generic_root, generic_review, sha)
    require_receipt(first, status="CHECKLIST_VERIFIED", sha=sha, count=128)
    second = bnf.verify(bnf_root, bnf_review, sha)
    require_receipt(second, status="BNF_CHECKLIST_VERIFIED", sha=sha, count=96)
    if aba_digests() != before or clean_head() != sha:
        raise CaptureError("checkout or native report changed during reconciliation")
    # Reuse the existing receipt formats without exposing paths, notes,
    # image bytes, source URLs, identity IDs, or operator information.
    return {
        "status": "PRIVATE_CHECKLISTS_RECONCILED",
        "git_sha": sha,
        "capture_count": 224,
        "native_aba_surfaces_per_configuration": 12,
        "generic_fingerprint": first["fingerprint"],
        "bnf_fingerprint": second["fingerprint"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("generic-root", "generic-review", "bnf-root", "bnf-review"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_matrix(args.generic_root, args.generic_review,
                                       args.bnf_root, args.bnf_review)))
    except (CaptureError, generic.ReviewError, OSError, ValueError,
            UnicodeError, subprocess.SubprocessError):
        print('{"status":"PRIVATE_CHECKLISTS_REFUSED"}')
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
