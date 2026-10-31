#!/usr/bin/env python3
"""Fail-closed Core test selection for a linear push to main.

Never replace an exhaustive manual run or broaden the trusted scope of the
existing PR Core selector. Unknown diffs select the full suite.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"^[0-9a-fA-F]{40}$")


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True,
        timeout=20, check=True,
    )
    return result.stdout


def _safe_path(path: str) -> bool:
    # Tests depend on shared fixtures, and the UI selector only owns UI
    # source changes. Unknown or shared paths keep the exhaustive suite.
    if path.startswith("tests/"):
        return path.startswith("tests/test_") and path.endswith(".py")
    if path.startswith(("src/cifereca/gui/", "src/cifereca/builtin_ui/", "e2e/")):
        return True
    if path in {"src/cifereca/app.py", "environnements/openai-chat-debian/capture_app.py"}:
        return True
    return path == "AGENTS.md" or path.startswith(("docs/", ".agents/"))


def select_push(
    root: Path, *, event: str, before: str, head: str,
    selector: Path | None = None,
) -> tuple[str, str, str]:
    """Return mode, pytest targets, explanation (never an unsafe skip)."""
    full = ("full", "tests")
    if event != "push":
        return *full, "manual-or-non-push"
    if not SHA.fullmatch(before or "") or set(before) == {"0"}:
        return *full, "missing-before-sha"
    if not SHA.fullmatch(head or "") or set(head) == {"0"}:
        return *full, "missing-head-sha"
    try:
        _git(root, "cat-file", "-e", f"{before}^{{commit}}")
        _git(root, "cat-file", "-e", f"{head}^{{commit}}")
        _git(root, "merge-base", "--is-ancestor", before, head)
        diff = _git(
            root, "diff", "--name-status", "-z", "--find-renames",
            "--no-ext-diff", before, head, "--",
        )
        fields = diff.split(b"\0")
        if fields[-1] != b"":
            return *full, "malformed-diff"
        fields.pop()
        if not fields or len(fields) % 2:
            return *full, "empty-or-malformed-diff"
        paths = []
        for status_raw, path_raw in zip(fields[::2], fields[1::2]):
            status = status_raw.decode("ascii")
            path = path_raw.decode("utf-8")
            if status not in {"A", "M"}:
                return *full, "renamed-deleted-or-unsupported-status"
            if any(ch in path for ch in "\n\r\t") or not _safe_path(path):
                return *full, "shared-or-ambiguous-path"
            paths.append(path)

        script = selector or root / "scripts/ci-select-core-tests.py"
        with tempfile.TemporaryDirectory() as temp:
            changed = Path(temp) / "changed-files.txt"
            changed.write_text("\n".join(paths) + "\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(script), str(changed)],
                cwd=root, capture_output=True, text=True, check=True, timeout=20,
            )
        output = result.stdout.strip().split("\t")
        if len(output) != 3:
            return *full, "invalid-selector-output"
        mode, targets, _e2e = output
        if mode not in {"changed", "ui", "ui-component"}:
            return *full, "selector-requires-full"
        selected = targets.split()
        if not selected or len(set(selected)) != len(selected):
            return *full, "empty-or-duplicate-targets"
        for item in selected:
            if not (
                item.startswith("tests/test_")
                and item.endswith(".py")
                and (root / item).is_file()
            ):
                return *full, "unknown-target"
        return mode, " ".join(selected), "verified-linear-push"
    except (
        OSError, subprocess.SubprocessError, UnicodeError, ValueError,
    ) as error:
        return *full, f"git-or-selector-unavailable:{type(error).__name__}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--event", default="")
    parser.add_argument("--before", default="")
    parser.add_argument("--head", default="")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    mode, targets, reason = select_push(
        args.root, event=args.event, before=args.before, head=args.head,
    )
    print(json.dumps({
        "core_mode": mode,
        "core_targets": targets,
        "selection_reason": reason,
    }, sort_keys=True))
    with args.github_output.open("a", encoding="utf-8") as handle:
        handle.write(f"core_mode={mode}\ncore_targets={targets}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
