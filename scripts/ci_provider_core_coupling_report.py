"""Explanatory (non-additive) Core/provider LOC report.

Preserves ci_provider_core_coupling_inventory.py v1 figures for trend comparison.
Unlike v1's text-substring 'generic LOC', classifies static provider-specific
AST signals, passive code mentions, comments/docstrings and unclassified code.
No code import/execution, I/O beyond local source inspection, or provider calls.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import io
import json
from pathlib import Path
import re
import tokenize
import sys

# Support both `python -m scripts....` and direct `python scripts/...py`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_provider_core_coupling_inventory import (
    DEFAULT_PROVIDERS, _LOC_PROVIDERS, _dedicated_provider,
    inspect_source, inventory,
)

_SIGNAL_KINDS = (
    "imports", "comparisons", "dispatch_tables", "dispatch_calls",
    "provider_method_calls", "provider_dynamic_calls",
)


def _comment_lines(text: str) -> set[int]:
    result: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                result.add(token.start[0])
    except (tokenize.TokenError, IndentationError):
        # AST already rejects syntactically invalid inputs.
        pass
    return result


def _docstring_lines(tree: ast.Module) -> set[int]:
    result: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            value = body[0]
            result.update(range(value.lineno, value.end_lineno + 1))
    return result


_AMBIGUOUS_ALIASES = frozenset({"local", "library", "catalog", "catalogue", "provider", "reference"})


def _installed_provider_aliases(root: Path) -> tuple[str, ...]:
    """Read installed plugin manifests without importing plugins.

    Detect new providers which the historical static five-provider counter
    does not know. Ambiguous one-word generic aliases are rejected; full
    dotted IDs are still eligible.
    """
    found = set(DEFAULT_PROVIDERS) | set(_LOC_PROVIDERS)
    for manifest in sorted((root / "src" / "plugins").glob("*/cifereca-plugin.json")):
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        for descriptor in payload.get("plugins", []):
            if not str(descriptor.get("type", "")).startswith("provider."):
                continue
            identifier = descriptor.get("id")
            if type(identifier) is not str or not identifier.startswith("provider."):
                continue
            full = identifier.casefold()
            found.add(full)
            suffix = full.removeprefix("provider.")
            if suffix not in _AMBIGUOUS_ALIASES:
                found.add(suffix)
            slug = manifest.parent.name.removeprefix("provider-").casefold()
            if slug and slug not in _AMBIGUOUS_ALIASES:
                found.add(slug)
    return tuple(sorted(found))


def _provider_pattern(names: tuple[str, ...]) -> re.Pattern[str]:
    # Provider strings in code, including whole identifier components and
    # aliases. Reject substring matches such as 'paris' in 'comparison'.
    return re.compile(
        r"(?<![a-z0-9])(?:" +
        "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True)) +
        r")(?![a-z0-9])", re.IGNORECASE,
    )


def extended_inventory(root: Path) -> dict[str, object]:
    """Return a mutually exclusive breakdown of all installed Core Python LOC.

    Signal lines are AST *indicators*, not a reachability or ownership proof.
    Physical lines in dedicated modules remain counted in 'dedicated' only.
    Uncategorized Core code remains visible so this report cannot look complete
    merely because it silently excluded all provider-neutral code.
    """
    historic = inventory(root)
    per_file: dict[str, dict[str, object]] = {}
    totals: Counter[str] = Counter()
    signals_by_kind: Counter[str] = Counter()
    signal_by_provider: Counter[str] = Counter()
    candidates: dict[str, dict[str, object]] = {}
    provider_aliases = _installed_provider_aliases(root)
    pattern = _provider_pattern(provider_aliases)
    candidate_buckets: Counter[str] = Counter()
    source_root = root / "src" / "cifereca"
    for path in sorted(source_root.rglob("*.py")) if source_root.is_dir() else ():
        rel = path.relative_to(root).as_posix()
        if rel in historic["parse_errors"]:
            continue
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines()
        tree = ast.parse(content)
        dedicated = _dedicated_provider(path.relative_to(root))
        totals["core_files"] += 1
        totals["core_physical_lines"] += len(lines)
        if dedicated:
            totals["dedicated_physical_lines"] += len(lines)
            continue

        signals = inspect_source(content, providers=provider_aliases)
        signal_lines: set[int] = set()
        for kind in _SIGNAL_KINDS:
            hits = {int(f["line"]) for f in signals[kind]}
            signal_lines.update(hits)
            signals_by_kind[kind] += len(hits)
            for entry in signals[kind]:
                signal_by_provider.update(entry["providers"])
        comments = _comment_lines(content)
        docstrings = _docstring_lines(tree)
        buckets: Counter[str] = Counter()
        mention_by_provider: Counter[str] = Counter()
        for index, line in enumerate(lines, 1):
            if index in signal_lines:
                buckets["ast_signal"] += 1
                continue
            if not line.strip():
                buckets["blank"] += 1
                continue
            if index in docstrings:
                if pattern.search(line):
                    buckets["passive_docstring"] += 1
                else:
                    buckets["other_docstring"] += 1
                continue
            if line.lstrip().startswith("#"):
                if pattern.search(line):
                    buckets["passive_comment"] += 1
                else:
                    buckets["other_comment"] += 1
                continue
            # A trailing comment with a vendor name alone must not turn
            # generic executable code into vendor-specific logic.
            code = line.split("#", 1)[0] if index in comments else line
            if pattern.search(code):
                buckets["passive_code_mention"] += 1
                for matched in set(m.group(0).casefold() for m in pattern.finditer(code)):
                    mention_by_provider[matched] += 1
            elif index in comments and pattern.search(line):
                buckets["passive_comment"] += 1
            else:
                buckets["other_code"] += 1
        for key, value in buckets.items():
            totals[key] += value
        if sum(buckets.values()) != len(lines):
            raise AssertionError("core_line_classification_incomplete")
        if buckets["ast_signal"] or buckets["passive_code_mention"] or buckets["passive_comment"] or buckets["passive_docstring"]:
            per_file[rel] = {
                "physical_lines": len(lines),
                "ast_signal_lines": buckets["ast_signal"],
                "passive_code_mention_lines": buckets["passive_code_mention"],
                "passive_comment_lines": buckets["passive_comment"],
                "passive_docstring_lines": buckets["passive_docstring"],
                "signal_kinds": {k: len({e["line"] for e in signals[k]})
                                 for k in _SIGNAL_KINDS if signals[k]},
                "passive_code_mentions_by_alias": dict(sorted(mention_by_provider.items())),
            }
        # Whole provider identifiers in module or directory names outside the
        # v1 dedicated scope are *review candidates*, never silently added LOC.
        if any(pattern.search(part) for part in path.relative_to(source_root).parts):
            candidate_buckets.update(buckets)
            candidates[rel] = {
                "physical_lines": len(lines),
                "ast_signal_lines": buckets["ast_signal"],
            }

    partition = (totals["dedicated_physical_lines"] +
                 sum(totals[k] for k in (
                     "ast_signal", "blank", "passive_docstring",
                     "other_docstring", "passive_comment", "other_comment",
                     "passive_code_mention", "other_code")))
    if partition != totals["core_physical_lines"]:
        raise AssertionError("core_physical_lines_partition_mismatch")
    expanded_named_lines = sum(x["physical_lines"] for x in candidates.values())
    expanded_generic_signal_lines = totals["ast_signal"] - candidate_buckets["ast_signal"]
    expanded_named_total = totals["dedicated_physical_lines"] + expanded_named_lines
    return {
        "metric": "core.provider.explanatory-loc/2",
        "historical": {
            "dedicated_lines": historic["dedicated_lines"],
            "dedicated_by_provider": historic["dedicated_by_provider"],
            "generic_provider_literal_lines_unique": historic[
                "generic_provider_literal_lines_unique"],
            "generic_files_with_provider_literal": historic[
                "generic_files_with_provider_literal"],
            "parse_errors": historic["parse_errors"],
        },
        "installed_provider_aliases_scanned": list(provider_aliases),
        "total_core_python_files": totals["core_files"],
        "total_core_python_physical_lines": totals["core_physical_lines"],
        "line_partition": {
            "dedicated_modules": totals["dedicated_physical_lines"],
            "generic_ast_signal": totals["ast_signal"],
            "generic_passive_code_mentions": totals["passive_code_mention"],
            "generic_passive_comments": totals["passive_comment"],
            "generic_passive_docstrings": totals["passive_docstring"],
            "generic_other_code": totals["other_code"],
            "generic_other_comments": totals["other_comment"],
            "generic_other_docstrings": totals["other_docstring"],
            "generic_blank": totals["blank"],
        },
        "expanded_named_modules": {
            "legacy_named_lines": totals["dedicated_physical_lines"],
            "additional_named_lines": expanded_named_lines,
            "all_named_lines": expanded_named_total,
            "additional_named_files": len(candidates),
            "all_named_files": historic["dedicated_files"] + len(candidates),
        },
        "remaining_generic_after_expanded_named_scope": {
            "ast_signal_lines": expanded_generic_signal_lines,
            "passive_code_mention_lines": (
                totals["passive_code_mention"] -
                candidate_buckets["passive_code_mention"]),
            "passive_comment_lines": (
                totals["passive_comment"] - candidate_buckets["passive_comment"]),
            "passive_docstring_lines": (
                totals["passive_docstring"] - candidate_buckets["passive_docstring"]),
        },
        "generic_ast_signal_occurrences_by_kind": dict(sorted(signals_by_kind.items())),
        "generic_ast_signal_occurrences_by_provider_alias": dict(sorted(signal_by_provider.items())),
        "generic_reported_files": len(per_file),
        "generic_files": dict(sorted(per_file.items())),
        "vendor_named_modules_outside_legacy_dedicated_scope": candidates,
        "top_generic_by_ast_signal": sorted(
            ({"path": p, **v} for p, v in per_file.items()),
            key=lambda e: (-e["ast_signal_lines"], -e["passive_code_mention_lines"], e["path"]),
        )[:30],
        "interpretation": (
            "Expanded named modules are a second candidate perimeter, not a "
            "claim of reachable provider logic. Exclude their lines from all "
            "remaining generic buckets before addition. AST scanning also "
            "uses installed provider manifests and may discover other aliases. "
            "Only dedicated_modules and the generic buckets partition all Core "
            "physical lines. Historical 'generic LOC' are raw substring-matching "
            "lines and overlap the buckets; DO NOT add them to the partition. "
            "AST signal lines indicate static coupling candidates, not reachability; "
            "passive mentions and vendor-named modules require manual review. "
            "Plugin package LOC and tests are excluded."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = extended_inventory(args.root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        history = report["historical"]
        print("Legacy dedicated LOC:", history["dedicated_lines"])
        print("Legacy generic mention LOC (not necessarily coupling):",
              history["generic_provider_literal_lines_unique"])
        for key, value in report["line_partition"].items():
            print(key + ":", value)
        print("Core physical LOC:", report["total_core_python_physical_lines"])
        print("Top signal files:")
        for row in report["top_generic_by_ast_signal"][:15]:
            print(" ", row["path"], "AST:", row["ast_signal_lines"],
                  "passive code:", row["passive_code_mention_lines"])
    return int(bool(report["historical"]["parse_errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
