"""#5975: never equate lexical/AST provider signals with live business debt."""
import json

import pytest

from scripts.ci_provider_business_coupling import (
    METRIC, compare, compare_cohort, inventory, main,
)


@pytest.fixture(scope="module")
def installed_core_inventory():
    """One read-only inventory of the unchanged checkout per pytest worker."""
    from pathlib import Path

    return inventory(Path(__file__).resolve().parents[1])


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
    captured = capsys.readouterr()
    assert json.loads(captured.out)["stale_reviews"] == ["site-nonexistent"]
    assert "Stale business-review IDs:" in captured.err
    assert "site-nonexistent" in captured.err


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
    # #6149 removed the public W1 non-Kobo canonical mirror. The two Kobo
    # AST safety-site IDs vanish or rebind, but the *same safety contract*
    # remains provider-neutral, and earns no fictitious cohort retirement.
    old_guards = {
        "site-dd2ac28f46a8a720315de122",
        "site-c668504ada66eace309e3e7f",
    }
    removed_writer = "site-1220521b4d5dc767addb6d29"
    removed_supplier_guard = "site-b4a6753e2f0ac2d6b78c0992"
    assert not old_guards.intersection(row["id"] for row in ledger["reviews"])
    assert all(row["id"] not in (removed_writer, removed_supplier_guard)
               for row in ledger["reviews"])
    retired = [row for row in ledger["retirements"] if row["id"] ==
               "struct-w1-all-provider-live-canonical-mirror-retired"]
    assert len(retired) == 1
    assert retired[0]["original_classification"] == "legacy_mutating_cutover"
    assert retired[0]["in_ast_metric"] is False
    assert retired[0]["retired_at_sha"] == (
        "d503d0659f5707544828df5dffaa1bdd85815111"
    )
    assert retired[0]["pr"] == "#6149"
    assert all((root / test).is_file() for test in retired[0]["tests"])
    guard = [row for row in ledger["guard_installations"] if row["id"] ==
             "guard-w1-universal-archive-only-and-owner-validation"]
    assert len(guard) == 1
    assert guard[0]["classification"] == "safety_guard_pending_contract"
    assert "v2->v3" in guard[0]["remaining_debt"]
    assert all((root / test).is_file() for test in guard[0]["tests"])
    # #6152 retires the last versioned v3 positive importer. Its historic
    # name must no longer be counted as active, but its reviewed physical
    # retirement and tests remain in the ledger.
    assert not any(row["id"] == "legacy-sqlite-v3-non-kobo-backup-import-writer"
                   for row in ledger["retained_legacy_migrations"])
    v3 = [row for row in ledger["retirements"] if row["id"] ==
          "struct-v3-all-provider-legacy-backup-import-writer-retired"]
    assert len(v3) == 1 and v3[0]["original_classification"] == "legacy_mutating_cutover"
    assert v3[0]["pr"] == "#6152"
    assert all((root / test).is_file() for test in v3[0]["tests"])
    assert all(row["id"] != "site-5a3051124e11f87bf24ccfb6"
               for row in ledger["reviews"])
    for id in ("struct-w1-supplier-only-archive-veto-replaced-neutral",
               "struct-w1-supplier-collision-helper-generalized"):
        matches = [row for row in ledger["retirements"] if row["id"] == id]
        assert len(matches) == 1
        assert matches[0]["in_ast_metric"] is False
        assert matches[0]["original_site_id"] in old_guards

    replacement = [
        row for row in ledger["retirements"]
        if row["id"] ==
        "struct-w1-kobo-only-source-archive-veto-replaced-neutral"
    ]
    assert len(replacement) == 1
    assert replacement[0].get("in_ast_metric") is False
    assert replacement[0].get("original_site_id") is None
    assert "AST-ID rebinding" in replacement[0]["evidence"]
    assert "4d29509441374a11ac51e7a6a8a474ee963b73b4" == (
        replacement[0]["retired_at_sha"]
    )
    assert replacement[0]["original_classification"] == (
        "safety_guard_pending_contract"
    )
    assert replacement[0]["pr"] == "#6138"
    neutral_source_guard = [
        row for row in ledger["guard_installations"]
        if row["id"] ==
        "guard-w1-all-provider-exact-typed-source-before-legacy-sql"
    ]
    assert len(neutral_source_guard) == 1
    assert neutral_source_guard[0]["pr"] == "#6138"
    assert neutral_source_guard[0]["classification"] == "safety_guard_pending_contract"
    assert all((root / test).is_file() for test in neutral_source_guard[0]["tests"])

    dead_veto = [
        row for row in ledger["retirements"]
        if row["id"] ==
        "struct-w1-unreachable-typed-source-mirror-veto-retired"
    ]
    assert len(dead_veto) == 1
    assert dead_veto[0]["in_ast_metric"] is True
    assert dead_veto[0]["original_site_id"] == (
        "site-7313c6ad37cfecdb7936e8bc"
    )
    assert dead_veto[0]["retired_at_sha"] == (
        "2dbfdb6c3bf62de9036fc1891f9aeeadbccb773a"
    )
    assert dead_veto[0]["pr"] == "#6132"
    assert dead_veto[0]["original_classification"] == (
        "safety_guard_pending_contract"
    )
    assert all((root / test).is_file() for test in dead_veto[0]["tests"])

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
    """Three Paris Settings brand sites retired; runtime producer guard remains."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    payload = json.loads(
        (root / "docs/provider-business-coupling-review.json").read_text(
            encoding="utf-8"
        )
    )
    provider_producer = "site-62d608c21d04153e918b5bb3"
    remaining = {
        row["id"]: row for row in payload["reviews"]
        if row["id"] == provider_producer
    }
    assert len(remaining) == 1
    row = remaining[provider_producer]
    assert row["classification"] == "safety_guard_pending_contract"
    assert row["reachability"] == "productive"
    assert row["productive_caller"] and row["effect"]
    assert row["replacement_contract"] and row["authority"]
    assert all((root / path).is_file() for path in row["tests"])
    assert not row["retirement_pr"]

    removed = {
        "site-48985b6dd27a2ea911e7026d": "presentation_only",
        "site-0d55ea2282fddc404d954e89": "safety_guard_pending_contract",
        "site-1e0a66caf63d7b19feef68dd": "safety_guard_pending_contract",
    }
    assert not removed.keys() & {row["id"] for row in payload["reviews"]}
    cutovers = [
        row for row in payload["retirements"]
        if row.get("original_site_id") in removed
    ]
    assert len(cutovers) == 3
    assert {row["original_site_id"]: row["original_classification"]
            for row in cutovers} == removed
    assert all(row["pr"] == "#6464" and row["in_ast_metric"] is True
               and row["classification"] == "neutral_source_scoped_coreui_cutover"
               and all((root / path).is_file() for path in row["tests"])
               for row in cutovers)
    # The old lexical TaskRunner lambda was relocated by #6364; #6464
    # retires the branded *business dispatch*, not the bound-method guard.
    old_call = "site-6df69f482e5a9d7f97c2be97"
    guard = next(
        row for row in payload["guard_installations"]
        if row["id"] == "guard-settings-adept-click-bound-runtime-probe-6364"
    )
    assert guard["classification"] == "safety_guard_pending_contract"
    assert old_call in guard["remaining_debt"]
    assert "check_source_adept_runtime" in guard["effect"]
    assert guard["pr"] == "#6364"
    assert all((root / path).is_file() for path in guard["tests"])
    assert not any(row.get("original_site_id") == old_call
                   for row in payload["retirements"])


def test_retired_w1_completion_marker_structural_evidence():
    """A deleted metadata writer is audited, not counted as canonical B cutover."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ledger = json.loads(
        (root / "docs/provider-business-coupling-review.json").read_text(
            encoding="utf-8"
        )
    )
    rows = [
        row for row in ledger["retirements"]
        if row["id"] ==
        "struct-w1-completed-archive-metadata-marker-writer-retired"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["original_classification"] == "legacy_mutating_cutover"
    assert row["classification"] == (
        "retired_active_obsolete_w1_metadata_marker_writer"
    )
    assert row["pr"] == "#6136"
    assert all((root / path).is_file() for path in row["tests"])
    assert row["evidence"] and row["replacement"]
    # No unverified AST-cohort retirement credit: this is a reviewed SQL
    # writer removal, with the lexical metric measured separately on CI.
    assert row.get("in_ast_metric") is not True


def test_versioned_historical_writer_survives_vendor_ast_selector_removal(tmp_path):
    """An AST reduction must not silently turn an active legacy writer into zero debt."""
    path = "src/cifereca/core/legacy.py"
    write(tmp_path, path, "def repair(provider):\n    return provider == 'kobo'\n")
    before = inventory(tmp_path, head_sha="old")
    assert before["raw_ast_unique_lines"] == 1
    write(tmp_path, path, "def repair(provider, historic_cohort):\n    return provider in historic_cohort\n")
    retained = {
        "id": "legacy-v1-repair", "classification": "legacy_mutating_cutover",
        "journey": "historical upgrade", "productive_caller": "Config.load",
        "effect": "still mutates old rows", "authority": "frozen persisted cohort",
        "replacement_contract": "sunset only after evidence",
        "tests": ["tests/test_legacy.py"], "pr": "#6141",
    }
    registry = tmp_path / "ledger.json"
    registry.write_text(json.dumps({
        "metric": METRIC, "reviews": [], "retained_legacy_migrations": [retained],
    }), encoding="utf-8")
    after = inventory(tmp_path, registry=registry, head_sha="new")
    assert after["raw_ast_unique_lines"] == 0
    assert after["active_legacy_writers_to_retire"] == 1
    assert after["retained_non_ast_legacy_migration_writers"] == 1
    assert after["retained_non_ast_legacy_migration_ids"] == ["legacy-v1-repair"]
    assert compare(before, after)["retained_non_ast_legacy_migrations_added"] == [
        "legacy-v1-repair",
    ]
    assert compare_cohort(before, after)["retired_reviewed"] == 0

    retained["classification"] = "legitimate_readonly_compat"
    registry.write_text(json.dumps({
        "metric": METRIC, "reviews": [], "retained_legacy_migrations": [retained],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_retained_legacy_migration_classification"):
        inventory(tmp_path, registry=registry)


def test_real_081_migration_is_explicitly_retained_after_genericization(installed_core_inventory):
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "docs/provider-business-coupling-review.json")
                        .read_text(encoding="utf-8"))
    assert all(row["id"] != "site-e3fc978e29d69c2c875be9dc"
               for row in ledger["reviews"])
    paths = [row for row in ledger["retained_legacy_migrations"]
             if row["id"] == "legacy-config-081-cardinality-versioned-writer"]
    assert len(paths) == 1
    assert paths[0]["classification"] == "legacy_mutating_cutover"
    assert all((root / path).is_file() for path in paths[0]["tests"])
    physical = [row for row in ledger["retirements"]
                if row["id"] == "struct-config-081-cardinality-supplier-predicate-neutralized"]
    assert len(physical) == 1
    assert physical[0]["in_ast_metric"] is False
    assert physical[0]["original_site_id"] == "site-e3fc978e29d69c2c875be9dc"
    actual = installed_core_inventory
    # Config 0.8.1, SQLite v5 and newly inventoried v8 mutate old data;
    # the unrelated SQLite v3 importer was physically retired in #6152.
    # v5 and v8 are historical writers; none is a new regression.
    assert actual["retained_non_ast_legacy_migration_writers"] == 3
    assert actual["active_legacy_writers_to_retire"] >= 3
    assert actual["retained_non_ast_legacy_migration_ids"] == [
        "legacy-config-081-cardinality-versioned-writer",
        "legacy-sqlite-v5-kobo-cardinality-identity-repair",
        "legacy-sqlite-v8-paris-primary-account-receipt-repair",
    ]
    assert not actual["stale_reviews"]


def test_w1_sourcekey_preflight_is_provider_neutral_and_physically_reviewed():
    import inspect
    from pathlib import Path
    from cifereca.storage import Storage

    source = inspect.getsource(Storage.backup_put)
    admission = source.split('now = int(item.get("updated_at")', 1)[0]
    assert 'if item.get("provider_id") == "kobo"' not in admission
    assert "backup_legacy_item_id_invalid" in admission
    assert "backup_legacy_path_invalid" in admission
    assert "type(key) is not str" in admission

    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "docs/provider-business-coupling-review.json")
                        .read_text(encoding="utf-8"))
    old = "site-dd2ac28f46a8a720315de122"
    # #6149 physically replaced the last Kobo-only archive guard with the
    # neutral W1 path. The old AST ordinal is no longer a reviewed live site:
    # it is not evidence that the safety contract itself was retired.
    assert all(row["id"] != old for row in ledger["reviews"])
    neutral = [row for row in ledger["guard_installations"] if row["id"] ==
               "guard-w1-universal-archive-only-and-owner-validation"]
    assert len(neutral) == 1
    assert neutral[0]["pr"] == "#6149"
    assert all(row["id"] != "site-d88574f6e28d059189c71da5"
               for row in ledger["reviews"])
    retired = [row for row in ledger["retirements"] if row["id"] ==
               "struct-w1-all-provider-sourcekey-admission-supplier-predicate-retired"]
    assert len(retired) == 1
    assert retired[0]["original_site_id"] == old
    assert retired[0]["original_classification"] == "safety_guard_pending_contract"
    assert retired[0]["in_ast_metric"] is False
    assert retired[0]["rebound_site_id"] == old
    assert retired[0]["retained_guard_previous_id"] == (
        "site-d88574f6e28d059189c71da5"
    )
    assert retired[0]["retired_at_sha"] == (
        "f9efeabf8358e268d7bfcc71e5b9e1358acecd0c"
    )
    assert retired[0]["pr"] == "#6145"
    guards = [row for row in ledger["guard_installations"] if row["id"] ==
              "guard-w1-all-provider-legacy-sourcekey-path-admission"]
    assert len(guards) == 1
    assert guards[0]["classification"] == "safety_guard_pending_contract"
    assert "migration" in guards[0]["remaining_debt"] or "archive" in guards[0]["remaining_debt"]
    assert all((root / test).is_file() for test in retired[0]["tests"])


def test_v3_legacy_writer_retired_without_importing_any_provider_right():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "docs/provider-business-coupling-review.json")
                        .read_text(encoding="utf-8"))
    retired = [row for row in ledger["retirements"]
               if row["id"] == "struct-v3-all-provider-legacy-backup-import-writer-retired"]
    assert len(retired) == 1
    assert retired[0]["original_classification"] == "legacy_mutating_cutover"
    assert retired[0]["in_ast_metric"] is False
    assert retired[0]["retired_at_sha"] == (
        "e7c8fd4ba28e63ad7688662177a4d2676ea6fc42"
    )
    assert retired[0]["pr"] == "#6152"
    assert all((root / test).is_file() for test in retired[0]["tests"])

    prior_guard = "site-dd9fadf5918f4eb2fda40376"
    assert all(row["id"] != prior_guard for row in ledger["reviews"])
    neutral = [row for row in ledger["guard_installations"]
               if row["id"] == "guard-v3-structural-only-all-provider-archives"]
    assert len(neutral) == 1
    assert neutral[0]["pr"] == "#6152"
    assert all((root / test).is_file() for test in neutral[0]["tests"])
    assert "legacy-sqlite-v3-non-kobo-backup-import-writer" not in {
        row["id"] for row in ledger["retained_legacy_migrations"]
    }
    source = (root / "src/cifereca/storage.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    migrate = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "_migrate")
    if3 = next(node for node in ast.walk(migrate)
               if isinstance(node, ast.If)
               and "version < 3" in ast.unparse(node.test))
    assert not any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "_mirror_backup_row"
        for n in ast.walk(if3)
    )
    assert all(
        not (isinstance(n, ast.Constant) and n.value == "kobo")
        for n in ast.walk(if3)
    )


def test_legacy_positive_mirror_body_retired_without_double_writer_credit(installed_core_inventory):
    import inspect
    from pathlib import Path
    from cifereca.storage import Storage

    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "docs/provider-business-coupling-review.json")
                        .read_text(encoding="utf-8"))
    records = [row for row in ledger["retirements"]
               if row["id"] == "struct-dead-positive-legacy-mirror-sql-physically-removed"]
    assert len(records) == 1
    row = records[0]
    assert row["original_classification"] == "unreachable_historical_positive_writer_body"
    assert row["classification"] == "removed_unreachable_legacy_code_not_additional_active_writer"
    assert row["in_ast_metric"] is False
    assert row["pr"] == "#6154"
    assert row["retired_at_sha"] == "626418337b8e93f0cbba5113f8ed486f3dcf2ec7"
    assert all((root / test).is_file() for test in row["tests"])

    source = inspect.getsource(Storage._mirror_backup_row)
    assert "backup_legacy_canonical_projection_retired" in source
    assert "INSERT INTO entitlements" not in source
    assert "INSERT INTO acquisition_ledger" not in source
    assert "INSERT INTO artifacts" not in source
    assert "INSERT INTO placements" not in source
    assert "INSERT INTO catalog_entries" not in source

    guards = [row for row in ledger["guard_installations"] if row["id"] ==
              "guard-w1-v3-no-positive-legacy-mirror-entry"]
    assert len(guards) == 1
    assert guards[0]["pr"] == "#6154"
    assert all((root / test).is_file() for test in guards[0]["tests"])
    current = installed_core_inventory
    assert current["retained_non_ast_legacy_migration_writers"] == 3
    assert current["retained_non_ast_legacy_migration_ids"] == [
        "legacy-config-081-cardinality-versioned-writer",
        "legacy-sqlite-v5-kobo-cardinality-identity-repair",
        "legacy-sqlite-v8-paris-primary-account-receipt-repair",
    ]
    assert not current["stale_reviews"]


def test_sqlite_v5_historical_data_repair_is_counted_separately_from_v3(installed_core_inventory):
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "docs/provider-business-coupling-review.json")
                        .read_text(encoding="utf-8"))
    live = [row for row in ledger["retained_legacy_migrations"] if row["id"] ==
            "legacy-sqlite-v5-kobo-cardinality-identity-repair"]
    assert len(live) == 1
    assert live[0]["classification"] == "legacy_mutating_cutover"
    assert live[0]["pr"] == "#6154"
    assert all((root / test).is_file() for test in live[0]["tests"])
    assert "legacy-sqlite-v3-non-kobo-backup-import-writer" not in {
        row["id"] for row in ledger["retained_legacy_migrations"]
    }
    source = (root / "src/cifereca/storage.py").read_text(encoding="utf-8")
    migrate = next(node for node in ast.walk(ast.parse(source))
                   if isinstance(node, ast.FunctionDef) and node.name == "_migrate")
    v5 = next(node for node in ast.walk(migrate)
              if isinstance(node, ast.If) and ast.unparse(node.test) == "version < 5")
    sql_literals = [
        node.value.lower() for node in ast.walk(v5)
        if isinstance(node, ast.Constant) and type(node.value) is str
    ]
    assert any("update provider_connections set identity_id" in s for s in sql_literals)
    assert any("delete from provider_accounts" in s for s in sql_literals)
    assert installed_core_inventory["active_legacy_writers_to_retire"] >= 3


def test_v5_supplier_selector_neutralized_without_legacy_writer_retirement(installed_core_inventory):
    import inspect
    from pathlib import Path
    import cifereca.storage as storage_module
    from cifereca.storage import Storage

    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "docs/provider-business-coupling-review.json")
                        .read_text(encoding="utf-8"))
    row = [entry for entry in ledger["retirements"] if entry["id"] ==
           "struct-v5-historical-cardinality-supplier-sql-selector-neutralized"]
    assert len(row) == 1
    assert row[0]["in_ast_metric"] is False
    assert row[0]["original_classification"] == "legacy_mutating_cutover"
    assert row[0]["pr"] == "#6162"
    assert row[0]["retired_at_sha"] == (
        "a97375df5d8b60465862238ca5f3a8841ee768e3"
    )
    assert all((root / test).is_file() for test in row[0]["tests"])

    assert storage_module._LEGACY_V5_SINGLE_CONNECTION_PROVIDERS == ("kobo",)
    migrate = inspect.getsource(Storage._migrate)
    data_repair = migrate.split(
        "# Fixed historical source cohort controls ONLY the v4->v5", 1
    )[1].split("# A legacy global ADE profile", 1)[0]
    assert "for legacy_provider_id in _LEGACY_V5_SINGLE_CONNECTION_PROVIDERS" in data_repair
    assert "WHERE provider_id='kobo'" not in data_repair
    assert "WHERE provider_id=?" in data_repair
    # v5's historical partial indexes remain until v15 converts them to
    # generic persistent SQL policy: this PR is NOT full writer retirement.
    assert "uq_kobo_connection_identity" in migrate
    live = [entry for entry in ledger["retained_legacy_migrations"]
            if entry["id"] == "legacy-sqlite-v5-kobo-cardinality-identity-repair"]
    assert len(live) == 1
    assert live[0]["classification"] == "legacy_mutating_cutover"
    assert len(ledger["retained_legacy_migrations"]) == 3
    assert installed_core_inventory["retained_non_ast_legacy_migration_writers"] == 3


def test_residual_adept_facade_signals_have_source_scoped_reviews():
    """Classification is not retirement: preserve actual active refusal sites."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    ledger = json.loads(
        (root / "docs/provider-business-coupling-review.json").read_text(
            encoding="utf-8"
        )
    )
    reviews = {row["id"]: row for row in ledger["reviews"]}
    expected = {
        "site-ce18a3f96ffb1a6a1ca88708": (
            "false_positive", "check_source_adept_runtime",
        ),
        "site-9db736c5a6459afb19e60a9a": (
            "safety_guard_pending_contract", "adept_return_status_for_loan",
        ),
    }
    for site_id, (classification, caller) in expected.items():
        row = reviews[site_id]
        assert row["classification"] == classification
        assert caller in row["productive_caller"]
        assert row["retirement_pr"] == ""
        assert row["tests"]
        assert all((root / path).is_file() for path in row["tests"])
        assert row["authority"] and row["replacement_contract"]
    # No new provider permission is earned by reviewing old names.
    assert not any(
        row["id"] in expected
        for row in ledger["retirements"]
    )
    source = (root / "src/cifereca/services.py").read_text(encoding="utf-8")
    for _classification, method in expected.values():
        assert f"def {method}(" in source


def test_retired_saved_paris_adept_facade_has_no_stale_ast_review():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "docs/provider-business-coupling-review.json").read_text())
    assert "site-a99f3f4da32247a6a6efec04" not in {r["id"] for r in ledger["reviews"]}
    source = (root / "src/cifereca/services.py").read_text()
    assert "def normal_paris_adept_status(" not in source
    assert "def check_normal_paris_adept_runtime(" not in source


def test_installed_core_has_no_unreviewed_or_active_supplier_business_debt(installed_core_inventory):
    """Owner 16/11: freeze the semantic cutover, not a raw AST quota.

    The separately inventoried one-time upgrade writers are allowed to open
    existing installations; they are not live provider authorization routes.
    A new signal must have an explicit reviewed responsibility, even if the
    net raw AST count is unchanged. Existing guards must not be deleted to
    satisfy this test.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    report = installed_core_inventory
    assert not report["ast_parse_errors"]
    assert not report["stale_reviews"]
    assert report["unclassified"] == 0, [
        site["id"] for site in report["sites"]
        if site["classification"] == "unclassified"
    ]
    assert report["active_business_decisions_to_migrate"] == 0
    assert (
        report["active_legacy_writers_to_retire"]
        == report["retained_non_ast_legacy_migration_writers"]
    )
    assert report["dedicated_candidate_physical_lines"] == 0, (
        report["dedicated_path_candidates"]
    )
