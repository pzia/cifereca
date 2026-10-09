"""Conservative plugin test selection shared by PR and push workflows."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


def select(changes: list[tuple[str, str]], root: Path) -> tuple[list[str], str]:
    targets = set()
    for status, path in changes:
        if path.startswith(("docs/", ".agents/")) or path == "AGENTS.md":
            continue
        match = re.fullmatch(r"src/plugins/([A-Za-z0-9_-]+)/tests/(?:[A-Za-z0-9_-]+/)*test_[A-Za-z0-9_]+\.py", path)
        if status not in {"A", "M"} or not match:
            return [], "full: product, manifest, helper, deletion or shared change"
        target = "src/plugins/" + match[1]
        if not (root / target / "cifereca-plugin.json").is_file():
            return [], "full: missing package manifest"
        if not (root / target / "tests").is_dir():
            return [], "full: missing package tests"
        targets.add(target)
    if not targets:
        return [], "full: no isolated test target"
    return sorted(targets), "targeted: only package test modules changed"


def git_changes(root: Path, base: str, head: str, event: str) -> list[tuple[str, str]]:
    if event not in {"push", "pull_request"}:
        raise ValueError("manual or unknown event")
    if any(not re.fullmatch(r"[0-9a-fA-F]{40}", sha) or set(sha) == {"0"} for sha in (base, head)):
        raise ValueError("missing or zero SHA")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL)
    if event == "push" and git("rev-parse", "HEAD").decode().strip() != head:
        raise ValueError("checkout is not the requested head")
    if event == "pull_request":
        git("merge-base", "--is-ancestor", head, "HEAD")
        base = git("merge-base", base, head).decode().strip()
    else:
        git("merge-base", "--is-ancestor", base, head)
    parts = git("diff", "--no-renames", "--name-status", "-z", base, head).decode("utf-8").split("\0")
    if parts[-1] == "":
        parts.pop()
    if len(parts) % 2:
        raise ValueError("invalid diff")
    return list(zip(parts[::2], parts[1::2]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--event", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--targets-file", type=Path)
    args = parser.parse_args()
    try:
        targets, reason = select(git_changes(args.root, args.base, args.head, args.event), args.root)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        targets, reason = [], "full: diff unavailable or ambiguous (" + type(error).__name__ + ")"
    print(json.dumps({"mode": "targeted" if targets else "full", "targets": targets, "reason": reason}))
    if args.targets_file:
        args.targets_file.write_text(" ".join(targets), encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write("plugin_targets=" + " ".join(targets) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
