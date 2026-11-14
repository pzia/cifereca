"""#5975: reviewed business coupling ledger, separate from the v1 raw AST counter.

This is an advisory inventory of *candidates*, not a reachability analysis.
Never infer a permission, live writer, or removable decision from an AST hit.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from scripts.ci_provider_ast_function_worklist import inventory as ast_inventory
from scripts.ci_provider_core_coupling_inventory import _dedicated_provider
from scripts.ci_provider_core_coupling_report import extended_inventory

METRIC = "core.provider.business-coupling/1"
CLASSES = frozenset({
    "must_migrate", "legacy_mutating_cutover",
    "safety_guard_pending_contract", "legitimate_readonly_compat",
    "presentation_only", "false_positive",
})
REVIEW_FIELDS = frozenset({
    "id", "classification", "journey", "productive_caller", "effect",
    "authority", "replacement_contract", "tests", "retirement_pr", "rationale",
})
DECISION_NODES = (
    ast.Import, ast.ImportFrom, ast.Compare, ast.Call, ast.Dict,
    ast.Match, ast.MatchValue, ast.MatchOr, ast.Subscript,
)


def _anchor(tree: ast.AST, line: int) -> ast.AST | None:
    """Smallest AST decision expression containing a signal's physical line."""
    candidates = [
        node for node in ast.walk(tree)
        if isinstance(node, DECISION_NODES)
        and getattr(node, "lineno", 0) <= line <= getattr(node, "end_lineno", 0)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda n: (
        n.end_lineno - n.lineno,
        getattr(n, "end_col_offset", 0) - getattr(n, "col_offset", 0),
        n.lineno,
    ))


def _digest(node: ast.AST | None, fallback: str) -> str:
    semantic = (ast.dump(node, annotate_fields=True, include_attributes=False)
                if node is not None else fallback.strip())
    return hashlib.sha256(semantic.encode("utf-8")).hexdigest()[:20]


def _signal_sites(root: Path, raw: dict) -> list[dict]:
    """Line-based evidence is grouped by AST anchor, never by provider alias."""
    trees: dict[str, ast.AST] = {}
    sources: dict[str, list[str]] = {}
    anchors: dict[tuple, dict] = {}
    for entry in raw["entries"]:
        path = entry["path"]
        if path not in trees:
            source = (root / path).read_text(encoding="utf-8")
            trees[path] = ast.parse(source, filename=path)
            sources[path] = source.splitlines()
        line = entry["line"]
        node = _anchor(trees[path], line)
        first_line = getattr(node, "lineno", line)
        fallback = sources[path][line - 1]
        digest = _digest(node, fallback)
        key = (path, entry["function"], digest, first_line)
        if key not in anchors:
            anchors[key] = {
                "path": path, "function": entry["function"],
                "journey": entry["responsibility"],
                "fingerprint": digest, "first_line": first_line,
                "signal_lines": [], "providers": set(), "kinds": set(),
            }
        site = anchors[key]
        site["signal_lines"].append(line)
        site["providers"].update(entry["providers"])
        site["kinds"].update(entry["kinds"])

    # Ordinal distinguishes independent repeated decisions with identical AST.
    # It does not encode any physical line number in the durable ID.
    ordinals: Counter[tuple] = Counter()
    result = []
    for key in sorted(anchors, key=lambda k: (k[0], k[1], k[3], k[2])):
        site = anchors[key]
        scope = (site["path"], site["function"], site["fingerprint"])
        ordinals[scope] += 1
        identity = "|".join((*scope, str(ordinals[scope])))
        site["id"] = "site-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        site["signal_lines"] = sorted(set(site["signal_lines"]))
        site["providers"] = sorted(site["providers"])
        site["kinds"] = sorted(site["kinds"])
        site["classification"] = "unclassified"
        result.append(site)
    return result


def _load_reviews(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    ledger = json.loads(path.read_text(encoding="utf-8"))
    if ledger.get("metric") != METRIC or not isinstance(ledger.get("reviews"), list):
        raise ValueError("invalid_business_review_ledger_schema")
    reviews = {}
    for entry in ledger["reviews"]:
        if not isinstance(entry, dict) or not REVIEW_FIELDS <= entry.keys():
            raise ValueError("incomplete_business_review_entry")
        if entry["classification"] not in CLASSES or not entry["id"]:
            raise ValueError("invalid_business_review_classification")
        if (any(not isinstance(entry[k], str) or not entry[k].strip()
                for k in REVIEW_FIELDS - {"tests", "retirement_pr"})
                or not isinstance(entry["tests"], list)
                or not entry["tests"]
                or any(not isinstance(t, str) or not t.strip() for t in entry["tests"])
                or not isinstance(entry["retirement_pr"], str)):
            raise ValueError("business_review_requires_traceable_evidence")
        if entry["id"] in reviews:
            raise ValueError("duplicate_business_review_id")
        reviews[entry["id"]] = entry
    return reviews


def inventory(root: Path, *, registry: Path | None = None,
              base_sha: str | None = None, head_sha: str | None = None) -> dict:
    root = root.resolve()
    raw = ast_inventory(root)
    if raw["parse_errors"]:
        raise ValueError("incomplete_AST_inventory")
    path = registry if registry is not None else root / "docs/provider-business-coupling-review.json"
    reviews = _load_reviews(path)
    sites = _signal_sites(root, raw)
    matched = set()
    for site in sites:
        review = reviews.get(site["id"])
        if review is not None:
            matched.add(site["id"])
            site.update({k: v for k, v in review.items() if k != "id"})
    stale = sorted(reviews.keys() - matched)

    loc = extended_inventory(root)
    dedicated = sorted({
        path.relative_to(root).as_posix()
        for path in (root / "src/cifereca").rglob("*.py")
        if _dedicated_provider(path.relative_to(root)) is not None
    } | set(loc["vendor_named_modules_outside_legacy_dedicated_scope"]))
    classes = Counter(site["classification"] for site in sites)
    total = len(sites)
    classified = total - classes["unclassified"]
    return {
        "metric": METRIC,
        "base_sha": base_sha, "head_sha": head_sha,
        "raw_ast_metric": raw["metric"],
        "raw_ast_unique_lines": raw["unique_lines"],
        "raw_ast_by_responsibility": raw["unique_lines_by_responsibility"],
        "ast_parse_errors": raw["parse_errors"],
        "candidate_sites": total,
        "active_business_decisions_to_migrate": classes["must_migrate"],
        "active_legacy_writers_to_retire": classes["legacy_mutating_cutover"],
        "safety_guards_pending_contract": classes["safety_guard_pending_contract"],
        "justified_retained": sum(classes[c] for c in (
            "legitimate_readonly_compat", "presentation_only", "false_positive")),
        "unclassified": classes["unclassified"],
        "review_coverage_pct": round(100 * classified / total, 2) if total else 100.0,
        "stale_reviews": stale,
        "dedicated_path_candidates": dedicated,
        "dedicated_candidate_paths_count": len(dedicated),
        # A vendor-named module is not proof of executable runtime coupling.
        # Null until the call graph for each dedicated path has been reviewed.
        "dedicated_runtime_paths_remaining": None,
        "dedicated_candidate_physical_lines": loc["expanded_named_modules"]["all_named_lines"],
        "sites": sites,
        "limits": (
            "AST hits and provider-named modules are candidate evidence only. "
            "Unclassified paths, indirect dispatch and unscanned dedicated-module "
            "functions must be reviewed before claiming 100% business coverage. "
            "A semantic fingerprint is relocation evidence, not proof of removal."
        ),
    }


def compare(baseline: dict, current: dict) -> dict:
    if baseline.get("metric") != METRIC or current.get("metric") != METRIC:
        raise ValueError("incompatible_business_baseline")
    before = {s["id"]: s for s in baseline["sites"]}
    after = {s["id"]: s for s in current["sites"]}
    lost = set(before) - set(after)
    gained = set(after) - set(before)
    # Potential moved/renamed sites have the same AST shape. No auto credit.
    removed_fingerprints = defaultdict(list)
    for key in lost:
        removed_fingerprints[before[key]["fingerprint"]].append(key)
    moves = [{
        "old_ids": sorted(removed_fingerprints[after[key]["fingerprint"]]),
        "new_id": key,
        "fingerprint": after[key]["fingerprint"],
    } for key in sorted(gained)
        if removed_fingerprints.get(after[key]["fingerprint"])]
    return {
        "base_sha": baseline.get("head_sha"),
        "head_sha": current.get("head_sha"),
        "raw_ast_delta": current["raw_ast_unique_lines"] - baseline["raw_ast_unique_lines"],
        "added_site_ids": sorted(gained), "removed_site_ids": sorted(lost),
        "potential_relocations": moves,
        "reclassifications": [
            {"id": key, "before": before[key]["classification"],
             "after": after[key]["classification"]}
            for key in sorted(before.keys() & after.keys())
            if before[key]["classification"] != after[key]["classification"]
        ],
        "dedicated_candidates_added": sorted(set(current["dedicated_path_candidates"])
                                             - set(baseline["dedicated_path_candidates"])),
        "dedicated_candidates_removed": sorted(set(baseline["dedicated_path_candidates"])
                                               - set(current["dedicated_path_candidates"])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--base-sha")
    parser.add_argument("--head-sha")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = inventory(args.root, registry=args.registry,
                           base_sha=args.base_sha, head_sha=args.head_sha)
        if args.baseline:
            result["comparison"] = compare(json.loads(args.baseline.read_text(encoding="utf-8")),
                                           result)
    except (ValueError, OSError, KeyError, TypeError, SyntaxError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("Business-coupling candidates:", result["candidate_sites"])
        for key in ("raw_ast_unique_lines", "active_business_decisions_to_migrate",
                    "active_legacy_writers_to_retire", "safety_guards_pending_contract",
                    "justified_retained", "unclassified", "review_coverage_pct",
                    "dedicated_candidate_paths_count"):
            print(f"{key}: {result[key]}")
        if result["stale_reviews"]:
            print("Stale review IDs:", result["stale_reviews"])
    return int(bool(result["stale_reviews"]))


if __name__ == "__main__":
    raise SystemExit(main())
