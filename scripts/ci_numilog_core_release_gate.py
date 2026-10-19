"""#2095: fail-closed Numilog release gate against the *published Core tag*.

This read-only test is not a release authorization. It must run after the
existing published-wheel identity/hash checks, never against source HEAD.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
from typing import Callable, Sequence

from packaging.version import InvalidVersion, Version

NUMILOG_ID = "org.cifereca.provider.numilog"
MIN_NUMILOG_VERSION = Version("0.1.51")
# Squash-merge SHAs of the actual Core / CoreUI fixes required by #2095:
# #2081 (safe Numilog projection), #2117/#2112 (existing ACSM indexing),
# #2125/#2114 (Search refresh after indexing). These MUST all be ancestors
# of the Core tag, not just present in the later plugins-only source HEAD.
REQUIRED_CORE_COMMITS = (
    "1c15770ca989c8a10ff09e963453cf647aff7364",
    "93a2d7044d57e1909e7b0685e1590ffaf2d9448b",
    "b1762e044efdb8adbed95b882be35dca9c5eb781",
)
_SHA = re.compile(r"[a-f0-9]{40}\Z", re.ASCII)


class NumilogReleaseRefused(ValueError):
    """Cannot attest the needed fixes in the published Core."""


def validate_numilog_release_core(
    selected_packages: Sequence[str],
    core_tag_commit: str,
    *,
    manifest_path: Path,
    checkout: Path,
    runner: Callable[..., object] = subprocess.run,
    required_commits: Sequence[str] = REQUIRED_CORE_COMMITS,
) -> str:
    """Verify the selected Numilog and the *published* Core tag's ancestry.

    The surrounding workflow independently checks GitHub Release metadata,
    the tag source SHA, SHA256SUMS, the exact downloaded Core wheel and
    installation. This function adds the specific Numilog fix requirements.
    """
    if NUMILOG_ID not in selected_packages:
        return "not-selected"
    if not isinstance(core_tag_commit, str) or not _SHA.fullmatch(core_tag_commit):
        raise NumilogReleaseRefused("Invalid published Core tag commit")
    try:
        obj = json.loads(manifest_path.read_text(encoding="utf-8"))
        package = obj["package"]
        if type(package) is not dict or package["id"] != NUMILOG_ID:
            raise ValueError("wrong package")
        raw_version = package["version"]
        if type(raw_version) is not str:
            raise ValueError("invalid version")
        version = Version(raw_version)
    except (OSError, KeyError, TypeError, ValueError, InvalidVersion):
        raise NumilogReleaseRefused("Numilog source manifest missing or invalid") from None
    if version < MIN_NUMILOG_VERSION:
        raise NumilogReleaseRefused("Numilog below required minimum version")
    for required in required_commits:
        if type(required) is not str or not _SHA.fullmatch(required):
            raise NumilogReleaseRefused("Invalid mandatory Core fix SHA")
        try:
            completed = runner(
                ["git", "merge-base", "--is-ancestor", required, core_tag_commit],
                cwd=checkout, check=False, capture_output=True,
            )
        except OSError:
            raise NumilogReleaseRefused("Published Core ancestry unavailable") from None
        if getattr(completed, "returncode", None) != 0:
            # This includes missing history, unknown commits and any git error.
            # Never fall back to the plugin branch's HEAD or VERSION equality.
            raise NumilogReleaseRefused(
                "Published Core tag does not contain all required Numilog fixes"
            )
    return "verified-core-ancestry"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-ids", required=True)
    parser.add_argument("--core-tag-commit", required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        result = validate_numilog_release_core(
            args.package_ids.split(","),
            args.core_tag_commit,
            manifest_path=args.root / "src/plugins/provider-numilog/cifereca-plugin.json",
            checkout=args.root,
        )
    except NumilogReleaseRefused as exc:
        print(f"Numilog/Core release blocked: {exc}")
        return 2
    print(f"Numilog/Core compatibility gate: {result}; not a release authorization")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
