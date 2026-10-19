"""Validate explicit plugins-only release requests without packaging or publishing.

CI/CD #2331. The archived request marker is intentionally non-publishable.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REQUEST = ROOT / ".github/plugins-only-release-request.json"
PLUGINS_DIR = ROOT / "src/plugins"
ACTIVE_KIND = "authorized-plugins-only-release"
INACTIVE_KIND = "no-pending-plugins-only-release"
TAG_RE = re.compile(r"plugins-v[0-9A-Za-z.+_-]+\Z")
PACKAGE_RE = re.compile(r"org\.cifereca\.[A-Za-z0-9._-]+\Z")


class ReleaseRequestRefused(ValueError):
    """A request is invalid or selects packages absent from the active tree."""


def active_package_ids(plugins_dir: Path = PLUGINS_DIR) -> set[str]:
    """Read package IDs from the current source manifests, never from archives."""
    manifests = sorted(plugins_dir.glob("*/cifereca-plugin.json"))
    if not manifests:
        raise ReleaseRequestRefused("No active plugin manifests")
    found: set[str] = set()
    for path in manifests:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            package_id = obj["package"]["id"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ReleaseRequestRefused("Invalid active plugin manifest") from exc
        if not isinstance(package_id, str) or not PACKAGE_RE.fullmatch(package_id):
            raise ReleaseRequestRefused("Invalid active package ID")
        if package_id in found:
            raise ReleaseRequestRefused("Duplicate active package ID")
        found.add(package_id)
    return found


def validate_selection(tag: Any, selected: Any, plugins_dir: Path = PLUGINS_DIR) -> tuple[str, list[str]]:
    if not isinstance(tag, str) or not TAG_RE.fullmatch(tag):
        raise ReleaseRequestRefused("Invalid plugins-only release tag")
    if not isinstance(selected, list) or not selected:
        raise ReleaseRequestRefused("At least one explicit package required")
    if any(not isinstance(x, str) or not PACKAGE_RE.fullmatch(x) for x in selected):
        raise ReleaseRequestRefused("Invalid package ID")
    if len(set(selected)) != len(selected):
        raise ReleaseRequestRefused("Duplicate selected packages")
    missing = set(selected) - active_package_ids(plugins_dir)
    if missing:
        # Never print arbitrary user-controlled IDs or private input values.
        raise ReleaseRequestRefused("Selected package absent from active inventory")
    return tag, selected


def resolve_request(
    event: str, request_path: Path = REQUEST, *,
    dispatch_tag: str = "", dispatch_packages: str = "",
    plugins_dir: Path = PLUGINS_DIR,
) -> tuple[str, list[str]] | None:
    if event == "push":
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ReleaseRequestRefused("Release request missing or invalid") from exc
        if not isinstance(request, dict):
            raise ReleaseRequestRefused("Release request must be an object")
        if request.get("kind") == INACTIVE_KIND:
            if request.get("release_tag") or request.get("package_ids"):
                raise ReleaseRequestRefused("Inactive request may not select a release")
            return None
        if request.get("kind") != ACTIVE_KIND:
            raise ReleaseRequestRefused("Release request not explicitly authorized")
        return validate_selection(request.get("release_tag"), request.get("package_ids"), plugins_dir)
    if event == "workflow_dispatch":
        return validate_selection(dispatch_tag, dispatch_packages.split(","), plugins_dir)
    raise ReleaseRequestRefused("Unexpected release event")


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight explicit plugins-only requests")
    parser.add_argument("--event", choices=("push", "workflow_dispatch"), required=True)
    parser.add_argument("--request", type=Path, default=REQUEST)
    parser.add_argument("--dispatch-tag", default="")
    parser.add_argument("--dispatch-packages", default="")
    parser.add_argument("--plugins-dir", type=Path, default=PLUGINS_DIR)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--github-env", type=Path)
    args = parser.parse_args()
    try:
        selection = resolve_request(
            args.event, args.request,
            dispatch_tag=args.dispatch_tag,
            dispatch_packages=args.dispatch_packages,
            plugins_dir=args.plugins_dir,
        )
    except ReleaseRequestRefused as exc:
        parser.exit(2, f"ERROR: {exc}\n")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as out:
            out.write(f"authorized={'true' if selection else 'false'}\n")
    if selection:
        tag, ids = selection
        if args.github_env:
            with args.github_env.open("a", encoding="utf-8") as out:
                out.write(f"RELEASE_TAG={tag}\nPACKAGE_IDS={','.join(ids)}\n")
        print(f"Authorized request validated against active inventory: {len(ids)} packages")
    else:
        print("No pending plugins-only release; publication skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
