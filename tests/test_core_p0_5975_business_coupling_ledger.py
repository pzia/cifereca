"""#5975: never equate lexical/AST provider signals with live business debt."""
import json

import pytest

from scripts.ci_provider_business_coupling import (
    METRIC, compare, compare_cohort, inventory, main,
)


def write(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_raw_ast_is_preserved_and_review_starts_unclassified(tmp_path):
    write(tmp_path, "src/cifereca/core/router.py",
          "def route(provider_id):\n"
          "    if provider_id == 'kobo':\n"
          "        return 1\n"
          "    if provider_id == 'kobo':\n"
          "        return 2\n")
    result = inventory(tmp_path)
    assert result["metric"] == METRIC
    assert result["raw_ast_unique_lines"] == 2
    assert result["candidate_sites"] == 2
    assert len(set(site["id"] for site in result["sites"])) == 2
    assert result["unclassified"] == 2
    assert result["active_business_decisions_to_migrate"] == 0
    assert result["review_coverage_pct"] == 0
    assert result["dedicated_runtime_paths_remaining"] is None


def test_site_id_is_independent_of_line_numbers(tmp_path):
    path = "src/cifereca/services.py"
    source = ("def route(provider_id):\n"
              "    return provider_id == 'paris'\n")
    write(tmp_path, path, source)
    previous = inventory(tmp_path)
    write(tmp_path, path, "\n\n" + source)
    current = inventory(tmp_path)
    assert [s["id"] for s in previous["sites"]] == [
        s["id"] for s in current["sites"]
    ]
    assert compare(previous, current)["removed_site_ids"] == []
    assert compare(previous, current)["raw_ast_delta"] == 0


def test_review_requires_full_trace_and_retains_unreviewed_sites(tmp_path):
    write(tmp_path, "src/cifereca/core/router.py",
          "def route(provider_id):\n"
          "    return provider_id == 'kobo'\n")
    original = inventory(tmp_path)
    site_id = original["sites"][0]["id"]
    ledger = tmp_path / "ledger.json"
    review = {
        "id": site_id, "classification": "must_migrate",
        "journey": "fulfillment", "productive_caller": "LibraryService.route",
        "effect": "selected network call", "authority": "hardcoded provider ID",
        "replacement_contract": "current provider capability v2",
        "tests": ["tests/test_route.py::test_provider_unknown"],
        "retirement_pr": "", "rationale": "active branch verified from callsite",
    }
    ledger.write_text(json.dumps({"metric": METRIC, "reviews": [review]}))
    classified = inventory(tmp_path, registry=ledger)
    assert classified["raw_ast_unique_lines"] == original["raw_ast_unique_lines"]
    assert classified["active_business_decisions_to_migrate"] == 1
    assert classified["review_coverage_pct"] == 100
    assert not classified["stale_reviews"]
    review["tests"] = []
    ledger.write_text(json.dumps({"metric": METRIC, "reviews": [review]}))
    with pytest.raises(ValueError, match="traceable_evidence"):
        inventory(tmp_path, registry=ledger)


def test_stale_review_is_visible_and_invalidates_cli(tmp_path, capsys):
    write(tmp_path, "src/cifereca/core/router.py",
          "def route(provider_id):\n"
          "    return provider_id == 'bnf'\n")
    review = {
        "id": "site-nonexistent", "classification": "false_positive",
        "journey": "catalogue", "productive_caller": "none",
        "effect": "none", "authority": "not consulted",
        "replacement_contract": "not applicable",
        "tests": ["tests/test_router.py::test_no_effect"],
        "retirement_pr": "", "rationale": "verified no productive callsites",
    }
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"metric": METRIC, "reviews": [review]}))
    result = inventory(tmp_path, registry=ledger)
    assert result["stale_reviews"] == ["site-nonexistent"]
    assert main(["--root", str(tmp_path), "--registry", str(ledger),
                 "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["stale_reviews"] == [
        "site-nonexistent"
    ]


def test_move_is_not_counted_as_executed_debt_removed(tmp_path):
    code = ("def route(provider_id):\n"
            "    return provider_id == 'numilog'\n")
    write(tmp_path, "src/cifereca/core/old.py", code)
    baseline = inventory(tmp_path, head_sha="base-sha")
    (tmp_path / "src/cifereca/core/old.py").unlink()
    write(tmp_path, "src/cifereca/core/new.py", code)
    current = inventory(tmp_path, head_sha="head-sha")
    delta = compare(baseline, current)
    assert delta["raw_ast_delta"] == 0
    assert len(delta["removed_site_ids"]) == 1
    assert len(delta["added_site_ids"]) == 1
    assert len(delta["potential_relocations"]) == 1
    assert delta["base_sha"] == "base-sha"


def test_named_paths_are_candidates_not_proven_runtime(tmp_path):
    write(tmp_path, "src/cifereca/gui/bnf_lending_controller.py",
          "def display():\n    return 'catalogue'\n")
    result = inventory(tmp_path)
    assert "src/cifereca/gui/bnf_lending_controller.py" in (
        result["dedicated_path_candidates"]
    )
    assert result["dedicated_candidate_paths_count"] == 1
    assert result["dedicated_runtime_paths_remaining"] is None
    assert result["active_business_decisions_to_migrate"] == 0


def _structural_ledger(*, retirements=(), guards=()):
    return {
        "metric": METRIC, "reviews": [],
        "retirements": list(retirements),
        "guard_installations": list(guards),
    }


def _retirement_evidence():
    return {
        "id": "struct-exact-history", "original": "legacy navigation fallback",
        "original_classification": "navigation_dependent_legacy_read",
        "replacement": "pinned exact identity", "classification": "removed_scope",
        "evidence": "no Core worker reads mutable GUI context",
        "tests": ["tests/test_exact_history.py"], "pr": "#1234",
        "in_ast_metric": False,
    }


def _guard_evidence():
    return {
        "id": "guard-exact-loan", "journey": "loan eligibility",
        "classification": "safety_guard_pending_contract",
        "effect": "reject an unbound borrow before GET",
        "remaining_debt": "new generic policy needed",
        "tests": ["tests/test_loan.py"], "pr": "#1235",
    }


def test_structural_ledger_counts_are_disjoint_from_unreviewed_ast(tmp_path):
    write(tmp_path, "src/cifereca/core/router.py",
          "def route(provider):\n    return provider == 'kobo'\n")
    before = inventory(tmp_path, head_sha="before")
    payload = _structural_ledger(
        retirements=[_retirement_evidence()], guards=[_guard_evidence()],
    )
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps(payload), encoding="utf-8")
    after = inventory(tmp_path, registry=ledger, head_sha="after")
    assert after["raw_ast_unique_lines"] == before["raw_ast_unique_lines"] == 1
    assert after["unclassified"] == 1
    assert after["review_coverage_pct"] == 0
    assert after["reviewed_structural_retirements_count"] == 1
    assert after["reviewed_structural_retirement_ids"] == ["struct-exact-history"]
    assert after["reviewed_guard_installations_count"] == 1
    assert after["reviewed_structural_retirements_by_class"] == {
        "navigation_dependent_legacy_read": 1,
    }
    delta = compare(before, after)
    assert delta["raw_ast_delta"] == 0
    assert delta["reviewed_structural_retirements_added"] == [
        "struct-exact-history"
    ]
    assert delta["reviewed_guard_installations_added"] == ["guard-exact-loan"]
    assert delta["removed_site_ids"] == []


@pytest.mark.parametrize("invalid", [
    {"retirements": "not a list"},
    {"retirements": [{"id": "incomplete"}]},
    {"retirements": [{**_retirement_evidence(), "tests": []}]},
    {"retirements": [{**_retirement_evidence(), "in_ast_metric": "no"}]},
    {"retirements": [_retirement_evidence(), _retirement_evidence()]},
    {"guard_installations": [{"id": "invalid"}]},
])
def test_invalid_structural_review_fails_closed(tmp_path, invalid):
    write(tmp_path, "src/cifereca/core/router.py",
          "def route(provider):\n    return provider == 'kobo'\n")
    payload = _structural_ledger()
    payload.update(invalid)
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed|incomplete|duplicate"):
        inventory(tmp_path, registry=ledger)


def test_reviewed_groups_require_explicit_reachability_and_never_merge_unreviewed(tmp_path):
    write(
        tmp_path, "src/cifereca/core/router.py",
        "def route(provider):\n"
        "    if provider == 'kobo':\n        return 1\n"
        "    if provider == 'bnf':\n        return 2\n"
        "    if provider == 'paris':\n        return 3\n",
    )
    original = inventory(tmp_path)
    assert original["candidate_sites"] == 3
    assert original["business_groups_inventoried"] == 3
    assert original["business_groups_unproven"] == 3
    reviews = []
    for site in original["sites"][:2]:
        reviews.append({
            "id": site["id"], "classification": "safety_guard_pending_contract",
            "journey": "historical migration", "productive_caller": "Core.router.route",
            "effect": "safe negative veto", "authority": "historical source only",
            "replacement_contract": "versioned generic source proof",
            "tests": ["tests/test_router.py"], "retirement_pr": "",
            "rationale": "explicit manually reviewed decision",
            "decision_group_id": "historical.archive-owner-guard",
            "reachability": "legacy_only",
        })
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"metric": METRIC, "reviews": reviews}))
    observed = inventory(tmp_path, registry=ledger)
    assert observed["raw_ast_unique_lines"] == original["raw_ast_unique_lines"] == 3
    assert observed["candidate_sites"] == 3
    assert observed["review_coverage_pct"] == 66.67
    assert observed["business_groups_inventoried"] == 2
    assert observed["business_groups_reviewed"] == 1
    assert observed["business_groups_unproven"] == 1
    assert observed["business_group_review_coverage_pct"] == 50
    assert len(next(g for g in observed["business_candidate_groups"]
                    if g["status"] == "reviewed")["site_ids"]) == 2
    # A reviewed lexical signal is not an automatic reachability proof.
    reviews[0]["reachability"] = "unproven"
    ledger.write_text(json.dumps({"metric": METRIC, "reviews": reviews}))
    unproven = inventory(tmp_path, registry=ledger)
    assert unproven["business_groups_reviewed"] == 0
    reviews[0].pop("reachability")
    ledger.write_text(json.dumps({"metric": METRIC, "reviews": reviews}))
    with pytest.raises(ValueError, match="incomplete_business_decision_group"):
        inventory(tmp_path, registry=ledger)


def test_storage_remaining_w1_guards_and_retired_w5_branches_are_visible():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ledger = json.loads(
        (root / "docs/provider-business-coupling-review.json").read_text(
            encoding="utf-8"
        )
    )
    guards = {
        "site-7313c6ad37cfecdb7936e8bc",
        "site-d88574f6e28d059189c71da5",
        "site-5a3051124e11f87bf24ccfb6",
        "site-c668504ada66eace309e3e7f",
    }
    removed_writer = "site-1220521b4d5dc767addb6d29"
    removed_supplier_guard = "site-b4a6753e2f0ac2d6b78c0992"
    reviews = [row for row in ledger["reviews"] if row["id"] in guards]
    assert {row["id"] for row in reviews} == guards
    assert all(
        row["id"] not in (removed_writer, removed_supplier_guard)
        for row in ledger["reviews"]
    )
    assert len(reviews) == 4
    assert {row["decision_group_id"] for row in reviews} == {
        "legacy-backup.w1-canonical-publication-veto",
        "legacy-backup.w1-owner-archive-protection",
    }
    assert {row["reachability"] for row in reviews} == {
        "legacy_only", "productive",
    }
    assert all(
        row["classification"] == "safety_guard_pending_contract"
        and row["effect"] and row["authority"] and row["replacement_contract"]
        and all((root / test).is_file() for test in row["tests"])
        for row in reviews
    )
    retirements = [
        row for row in ledger["retirements"]
        if row["id"] ==
        "struct-w5-all-provider-legacy-backup-path-normalization-writer-retired"
    ]
    assert len(retirements) == 1
    retirement = retirements[0]
    assert retirement["original_classification"] == "legacy_mutating_cutover"
    assert retirement["original_site_id"] == removed_writer
    assert retirement["retired_at_sha"] == "5c81ae0f8914c78eb63860e9436c637d7f6d5b1c"
    assert retirement["in_ast_metric"] is True
    assert retirement["pr"] == "#6118"
    assert retirement["replacement"] and retirement["evidence"]
    assert all((root / test).is_file() for test in retirement["tests"])

    typed_veto = [
        row for row in ledger["retirements"]
        if row["id"] == "struct-w5-neutral-typed-source-filename-veto"
    ]
    assert len(typed_veto) == 1
    assert typed_veto[0]["original_classification"] == (
        "safety_guard_pending_contract"
    )
    assert typed_veto[0]["original_site_id"] == removed_supplier_guard
    assert typed_veto[0]["retired_at_sha"] == (
        "a4807403f58faf4911a706d71d6453595021af2f"
    )
    assert typed_veto[0]["in_ast_metric"] is True
    assert typed_veto[0]["pr"] == "#6122"
    assert all((root / test).is_file() for test in typed_veto[0]["tests"])
    neutral_guard = [
        row for row in ledger["guard_installations"]
        if row["id"] == "guard-w5-neutral-typed-source-old-path-veto"
    ]
    assert len(neutral_guard) == 1
    assert neutral_guard[0]["classification"] == (
        "safety_guard_pending_contract"
    )
    assert neutral_guard[0]["pr"] == "#6122"
    assert all((root / test).is_file() for test in neutral_guard[0]["tests"])


def test_fixed_anchor_cohort_keeps_unproven_removed_sites_visible():
    anchor = {
        "metric": METRIC, "head_sha": "a" * 40,
        "sites": [
            {"id": "site-old", "fingerprint": "semantic-a",
             "path": "src/cifereca/core/a.py", "function": "f",
             "classification": "safety_guard_pending_contract"},
            {"id": "site-stays", "fingerprint": "semantic-b",
             "path": "src/cifereca/core/b.py", "function": "g",
             "classification": "unclassified"},
        ],
    }
    current = {
        "metric": METRIC, "head_sha": "b" * 40,
        "sites": [anchor["sites"][1]], "retirement_evidence": [],
    }
    cohort = compare_cohort(anchor, current)
    assert cohort["anchor_sha"] == "a" * 40
    assert cohort["cohort_site_count"] == 2
    assert cohort["retired_unreviewed"] == 1
    assert cohort["unreviewed_baseline_remaining"] == 2
    assert cohort["cohort_review_coverage_pct"] == 0
    # A vanished site gets credit only with exact stable ID and reviewed
    # physical retirement evidence. Do not use a generic removal assertion.
    current["retirement_evidence"] = [{
        "original_site_id": "site-old", "retired_at_sha": "c" * 40,
        "in_ast_metric": True, "pr": "#999",
        "evidence": "physical deletion checked against exact tree",
        "tests": ["tests/test_a.py"],
    }]
    revised = compare_cohort(anchor, current)
    assert revised["retired_reviewed"] == 1
    assert revised["cohort_review_coverage_pct"] == 50
    assert next(row for row in revised["sites"]
                if row["site_id"] == "site-old")["retirement_prs"] == ["#999"]


def test_fixed_anchor_cohort_never_credits_semantic_relocation_or_new_site():
    old = {
        "id": "site-1", "fingerprint": "same-AST",
        "path": "src/cifereca/core/old.py", "function": "old",
        "classification": "safety_guard_pending_contract",
    }
    moved = {
        **old, "id": "site-2",
        "path": "src/cifereca/core/renamed.py", "function": "renamed",
        "classification": "unclassified",
    }
    anchor = {"metric": METRIC, "head_sha": "a" * 40,
              "sites": [old]}
    current = {"metric": METRIC, "head_sha": "b" * 40,
               "sites": [moved], "retirement_evidence": [{
                   "original_site_id": "site-1",
                   "retired_at_sha": "c" * 40,
                   "in_ast_metric": True, "pr": "#100",
                   "evidence": "potentially bogus retirement",
                   "tests": ["tests/test_scope.py"],
               }]}
    cohort = compare_cohort(anchor, current)
    assert cohort["cohort_site_count"] == 2
    assert cohort["relocations_unresolved"] == 1
    assert cohort["retired_reviewed"] == 0
    assert cohort["new_unreviewed_since_baseline"] == 1
    assert cohort["cohort_review_coverage_pct"] == 0
    assert next(row for row in cohort["sites"]
                if row["site_id"] == "site-1")["possible_relocation_ids"] == [
        "site-2"
    ]


def test_fixed_anchor_cohort_refuses_unknown_revisions_or_missing_sha():
    anchor = {"metric": METRIC, "head_sha": "a" * 40, "sites": []}
    current = {"metric": METRIC, "head_sha": "", "sites": []}
    with pytest.raises(ValueError, match="requires_exact_sha"):
        compare_cohort(anchor, current)
    current["head_sha"] = "b" * 40
    current["metric"] = "not comparable"
    with pytest.raises(ValueError, match="incompatible_business_cohort_anchor"):
        compare_cohort(anchor, current)


def test_adept_settings_and_runtime_provenance_ast_review_is_traceable():
    """Five previously unknown signals remain protected, not counted as deletions."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    payload = json.loads(
        (root / "docs/provider-business-coupling-review.json").read_text(
            encoding="utf-8"
        )
    )
    expected = {
        "site-48985b6dd27a2ea911e7026d": "presentation_only",
        "site-e49e284729574d20512f4033": "safety_guard_pending_contract",
        "site-6df69f482e5a9d7f97c2be97": "safety_guard_pending_contract",
        "site-652521330913780fa6c34776": "safety_guard_pending_contract",
        "site-62d608c21d04153e918b5bb3": "safety_guard_pending_contract",
    }
    records = {
        row["id"]: row for row in payload["reviews"] if row["id"] in expected
    }
    assert set(records) == set(expected)
    assert {key: row["classification"] for key, row in records.items()} == expected
    assert len({row["decision_group_id"] for row in records.values()}) == 3
    assert {row["reachability"] for row in records.values()} == {"productive"}
    for row in records.values():
        assert row["productive_caller"] and row["effect"]
        assert row["replacement_contract"] and row["authority"]
        assert all((root / path).is_file() for path in row["tests"])
    assert all(not row["retirement_pr"] for row in records.values())
