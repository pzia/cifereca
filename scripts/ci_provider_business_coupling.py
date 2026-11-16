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
import sys
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
REACHABILITIES = frozenset({
    "productive", "legacy_only", "unproven", "unreachable_proven",
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
        has_group = "decision_group_id" in entry
        has_reachability = "reachability" in entry
        if has_group != has_reachability:
            raise ValueError("incomplete_business_decision_group")
        if has_group:
            import re
            if (type(entry["decision_group_id"]) is not str
                    or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,127}",
                                    entry["decision_group_id"]) is None
                    or entry["reachability"] not in REACHABILITIES):
                raise ValueError("invalid_business_decision_group")
        if entry["id"] in reviews:
            raise ValueError("duplicate_business_review_id")
        reviews[entry["id"]] = entry
    return reviews


def _load_reviewed_structural_evidence(path: Path) -> tuple[list[dict], list[dict], list[dict]]:
    """Validate the human-authored non-AST ledger; NEVER infer reachability.

    Historical removals and installed guards are independent audit evidence,
    not a subtractive delta from raw provider AST or a new grant of rights.
    """
    if not path.exists():
        return [], [], []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("metric") != METRIC:
        raise ValueError("invalid_business_review_ledger_schema")
    groups = (
        ("retirements", frozenset({
            "id", "original", "original_classification", "replacement",
            "classification", "evidence", "tests", "pr",
        })),
        ("guard_installations", frozenset({
            "id", "journey", "classification", "effect",
            "remaining_debt", "tests", "pr",
        })),
        ("retained_legacy_migrations", frozenset({
            "id", "classification", "journey", "productive_caller",
            "effect", "authority", "replacement_contract", "tests", "pr",
        })),
    )
    results = []
    seen: set[str] = set()
    for name, required in groups:
        entries = payload.get(name, [])
        if type(entries) is not list:
            raise ValueError("invalid_reviewed_structural_evidence")
        for row in entries:
            if type(row) is not dict or not required <= row.keys():
                raise ValueError("incomplete_reviewed_structural_evidence")
            if (any(type(row[field]) is not str or not row[field].strip()
                    for field in required - {"tests"})
                    or type(row["tests"]) is not list or not row["tests"]
                    or any(type(t) is not str or not t.strip()
                           for t in row["tests"])):
                raise ValueError("untraceable_reviewed_structural_evidence")
            if name == "retained_legacy_migrations" and row["classification"] != "legacy_mutating_cutover":
                raise ValueError("invalid_retained_legacy_migration_classification")
            if row["id"] in seen:
                raise ValueError("duplicate_reviewed_structural_evidence")
            seen.add(row["id"])
            if "in_ast_metric" in row and type(row["in_ast_metric"]) is not bool:
                raise ValueError("invalid_reviewed_ast_scope")
        results.append(entries)
    return results[0], results[1], results[2]


def _business_candidate_groups(sites: list[dict]) -> list[dict]:
    """Conservative advisory roll-up, not automated runtime reachability.

    Only a HUMAN-tagged decision_group_id can combine different AST sites;
    all others have separate candidate buckets. A reviewed AST anchor without
    a reviewed reachability trace remains group-unproven.
    """
    buckets: dict[str, list[dict]] = defaultdict(list)
    for site in sites:
        group_id = site.get("decision_group_id")
        key = group_id if group_id else "unproven:" + site["id"]
        buckets[key].append(site)
    result = []
    for key, rows in sorted(buckets.items()):
        covered = all(
            row["classification"] != "unclassified"
            and row.get("decision_group_id") == key
            and row.get("reachability") in REACHABILITIES - {"unproven"}
            for row in rows
        )
        result.append({
            "id": key,
            "status": "reviewed" if covered else "unproven",
            "site_ids": sorted(row["id"] for row in rows),
            "classifications": sorted({row["classification"] for row in rows}),
            "reachabilities": sorted({row.get("reachability", "unproven")
                                      for row in rows}),
            "reviewed_decision_group_id": covered,
        })
    return result


def inventory(root: Path, *, registry: Path | None = None,
              base_sha: str | None = None, head_sha: str | None = None) -> dict:
    root = root.resolve()
    raw = ast_inventory(root)
    if raw["parse_errors"]:
        raise ValueError("incomplete_AST_inventory")
    path = registry if registry is not None else root / "docs/provider-business-coupling-review.json"
    reviews = _load_reviews(path)
    retirements, installed_guards, retained_legacy = _load_reviewed_structural_evidence(path)
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
    groups = _business_candidate_groups(sites)
    reviewed_groups = sum(g["status"] == "reviewed" for g in groups)
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
        "business_candidate_groups": groups,
        "business_groups_inventoried": len(groups),
        "business_groups_reviewed": reviewed_groups,
        "business_groups_unproven": len(groups) - reviewed_groups,
        "business_group_review_coverage_pct": (
            round(100 * reviewed_groups / len(groups), 2)
            if groups else 100.0
        ),
        "active_business_decisions_to_migrate": classes["must_migrate"],
        # A source-specific predicate can become data-driven while its versioned
        # historical writer remains active. Count manually reviewed non-AST
        # migration writers separately, rather than crediting lexical removal.
        "active_legacy_writers_to_retire": classes["legacy_mutating_cutover"] + len(retained_legacy),
        "retained_non_ast_legacy_migration_writers": len(retained_legacy),
        "retained_non_ast_legacy_migration_ids": sorted(row["id"] for row in retained_legacy),
        "safety_guards_pending_contract": classes["safety_guard_pending_contract"],
        "justified_retained": sum(classes[c] for c in (
            "legitimate_readonly_compat", "presentation_only", "false_positive")),
        "unclassified": classes["unclassified"],
        "review_coverage_pct": round(100 * classified / total, 2) if total else 100.0,
        # Non-AST evidence is disclosed separately, never subtracted from raw
        # AST or asserted to be verified runtime reachability.
        "reviewed_structural_retirements_count": len(retirements),
        "reviewed_structural_retirement_ids": sorted(x["id"] for x in retirements),
        # Internal evidence feeds the cohort matcher; it is already published
        # as human-authored ledger data, never as inferred runtime reachability.
        "retirement_evidence": [
            {key: row.get(key) for key in (
                "id", "in_ast_metric", "original_site_id", "retired_at_sha",
                "pr", "evidence", "tests"
            )} for row in retirements
        ],
        "reviewed_structural_retirements_by_class": dict(sorted(
            Counter(x["original_classification"] for x in retirements).items()
        )),
        "reviewed_guard_installations_count": len(installed_guards),
        "reviewed_guard_installation_ids": sorted(x["id"] for x in installed_guards),
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
            "A semantic fingerprint is relocation evidence, not proof of removal. "
            "Reviewed structural retirements and installed guards are human-authored "
            "evidence only: their count is not a proven active-debt delta. "
            "Versioned historical writers may remain active after their supplier "
            "predicate disappears; retained non-AST writers are disclosed and "
            "included in active legacy debt until a verified writer cutover. "
            "Business candidate groups combine only explicitly human-tagged sites; "
            "an ungrouped candidate or unproven runtime reachability is never credited "
            "as reviewed, and the group inventory is not a complete call graph."
        ),
    }


def compare(baseline: dict, current: dict) -> dict:
    if baseline.get("metric") != METRIC or current.get("metric") != METRIC:
        raise ValueError("incompatible_business_baseline")
    before = {s["id"]: s for s in baseline["sites"]}
    after = {s["id"]: s for s in current["sites"]}
    prior_retirements = set(baseline.get("reviewed_structural_retirement_ids", ()))
    new_retirements = set(current.get("reviewed_structural_retirement_ids", ()))
    prior_guards = set(baseline.get("reviewed_guard_installation_ids", ()))
    new_guards = set(current.get("reviewed_guard_installation_ids", ()))
    prior_retained = set(baseline.get("retained_non_ast_legacy_migration_ids", ()))
    new_retained = set(current.get("retained_non_ast_legacy_migration_ids", ()))
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
        "reviewed_structural_retirements_added": sorted(new_retirements - prior_retirements),
        "reviewed_structural_retirements_removed": sorted(prior_retirements - new_retirements),
        "reviewed_guard_installations_added": sorted(new_guards - prior_guards),
        "reviewed_guard_installations_removed": sorted(prior_guards - new_guards),
        "retained_non_ast_legacy_migrations_added": sorted(new_retained - prior_retained),
        "retained_non_ast_legacy_migrations_removed": sorted(prior_retained - new_retained),
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


def compare_cohort(anchor: dict, current: dict) -> dict:
    """Conservative fixed-anchor cohort across reviewed and missing AST sites.

    Distinct from the per-PR delta. Disappearance is NOT retirement evidence.
    A site absent on HEAD is reviewed-retired only with a keyed, explicitly
    audited retirement record; shared fingerprints are potential relocation.
    This anchor/head comparison cannot see transient sites in intervening
    commits, so it must not be described as exhaustive historical reachability.
    """
    if anchor.get("metric") != METRIC or current.get("metric") != METRIC:
        raise ValueError("incompatible_business_cohort_anchor")
    if (type(anchor.get("head_sha")) is not str
            or type(current.get("head_sha")) is not str
            or not anchor["head_sha"] or not current["head_sha"]):
        raise ValueError("business_cohort_requires_exact_sha")
    before = {site["id"]: site for site in anchor["sites"]}
    after = {site["id"]: site for site in current["sites"]}
    introduced = set(after) - set(before)
    lost = set(before) - set(after)
    new_by_fingerprint = defaultdict(list)
    for site_id in introduced:
        new_by_fingerprint[after[site_id]["fingerprint"]].append(site_id)
    retirement_evidence = {}
    for row in current.get("retirement_evidence", []):
        old = row.get("original_site_id")
        commit = row.get("retired_at_sha")
        if (row.get("in_ast_metric") is True and type(old) is str
                and type(commit) is str and len(commit) == 40
                and all(char in "0123456789abcdef" for char in commit.lower())
                and type(row.get("pr")) is str
                and row["pr"].startswith("#")
                and row.get("evidence") and row.get("tests")):
            retirement_evidence.setdefault(old, []).append(row)
    states = []
    for site_id in sorted(set(before) | set(after)):
        site = after.get(site_id) or before[site_id]
        if site_id in after:
            state = ("active_reviewed"
                     if site["classification"] != "unclassified"
                     else "active_unreviewed")
        elif new_by_fingerprint.get(site["fingerprint"]):
            state = "relocated_pending"
        elif site_id in retirement_evidence:
            state = "retired_reviewed"
        else:
            state = "retired_unreviewed"
        states.append({
            "site_id": site_id, "state": state,
            "from_anchor": site_id in before,
            "path": site["path"], "function": site["function"],
            "fingerprint": site["fingerprint"],
            "possible_relocation_ids": sorted(
                new_by_fingerprint.get(site["fingerprint"], [])
            ) if site_id in lost else [],
            "retirement_prs": sorted(
                {row["pr"] for row in retirement_evidence.get(site_id, [])}
            ) if state == "retired_reviewed" else [],
        })
    counts = Counter(row["state"] for row in states)
    total = len(states)
    reviewed = counts["active_reviewed"] + counts["retired_reviewed"]
    return {
        "metric": "core.provider.business-coupling-cohort/1",
        "anchor_sha": anchor["head_sha"],
        "head_sha": current["head_sha"],
        "cohort_site_count": total,
        "active_reviewed": counts["active_reviewed"],
        "active_unreviewed": counts["active_unreviewed"],
        "retired_reviewed": counts["retired_reviewed"],
        "retired_unreviewed": counts["retired_unreviewed"],
        "relocated_pending": counts["relocated_pending"],
        "cohort_review_coverage_pct": round(
            100 * reviewed / total, 2
        ) if total else 100.0,
        "unreviewed_baseline_remaining": sum(
            row["from_anchor"] and row["state"] in (
                "active_unreviewed", "retired_unreviewed", "relocated_pending",
            ) for row in states
        ),
        "new_unreviewed_since_baseline": sum(
            not row["from_anchor"] and row["state"] == "active_unreviewed"
            for row in states
        ),
        "reviewed_retired": counts["retired_reviewed"],
        "relocations_unresolved": counts["relocated_pending"],
        "sites": states,
        "limits": (
            "Fixed anchor + current HEAD are compared; intermediate transient "
            "sites are not inventoried. Missing IDs do not prove retirement. "
            "Unreviewed and relocation-pending sites stay in the denominator. "
            "Retirement records are human-reviewed evidence, not runtime proof."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--cohort-baseline", type=Path)
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
        if args.cohort_baseline:
            result["cohort"] = compare_cohort(
                json.loads(args.cohort_baseline.read_text(encoding="utf-8")),
                result,
            )
    except (ValueError, OSError, KeyError, TypeError, SyntaxError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("Business-coupling candidates:", result["candidate_sites"])
        for key in ("raw_ast_unique_lines", "active_business_decisions_to_migrate",
                    "active_legacy_writers_to_retire", "safety_guards_pending_contract",
                    "justified_retained", "unclassified", "review_coverage_pct",
                    "reviewed_structural_retirements_count",
                    "reviewed_guard_installations_count",
                    "business_groups_inventoried",
                    "business_groups_reviewed",
                    "business_groups_unproven",
                    "business_group_review_coverage_pct",
                    "dedicated_candidate_paths_count"):
            print(f"{key}: {result[key]}")
        if "cohort" in result:
            c = result["cohort"]
            print("Cohort anchor:", c["anchor_sha"])
            print("Cohort reviewed:", c["cohort_review_coverage_pct"])
            print("Cohort relocation pending:", c["relocations_unresolved"])
        if result["stale_reviews"]:
            print("Stale review IDs:", result["stale_reviews"])
    # The JSON report is often redirected to a file in CI. A stale reviewed
    # semantic site must still name its offending IDs on the failure console.
    if result["stale_reviews"]:
        print("Stale business-review IDs:", result["stale_reviews"], file=sys.stderr)
        # P0_RELOCATION_DIAGNOSTIC: temporary site inventory for review migration.
        for site in result["sites"]:
            if site["path"] == "src/cifereca/gui/settings_editor_actions.py":
                print("REVIEW_RELOCATION_CANDIDATE", site["id"], site["function"], site["fingerprint"], site["signal_lines"], file=sys.stderr)
    return int(bool(result["stale_reviews"]))


if __name__ == "__main__":
    raise SystemExit(main())
