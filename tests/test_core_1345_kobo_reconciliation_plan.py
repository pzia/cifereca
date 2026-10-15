"""#1345: manual Kobo SHA reconciliation plan is bounded and non-executable."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.kobo_reconciliation_plan import kobo_manual_reconciliation_plan
from tests.test_core_1345_kobo_local_audit import _backup, _catalog, _no_mutation
from tests.test_kobo_backup_fault_isolation import _service, _write_epub


def _report(*, truncated=False, cases=None, counts=None):
    baseline = dict(
        backup_rows=2, linked_files_verified=2, unverified_placements=0,
        missing_or_unlinked=0, stored_hash_mismatch=0,
        unproven_catalogue_links=0, same_product_different_bytes=0,
        binary_match_groups=0, active_entitlements_unmaterialized=0,
    )
    baseline.update(counts or {})
    case_rows = list(cases or ())
    return {
        "schema": "kobo.local.reconciliation.audit/1",
        "scope": "single_account",
        "offline": True, "read_only": True, "dry_run": True,
        "downloads_authorized": False,
        "mutations_authorized": False,
        "truncated": truncated,
        "counts": baseline,
        "accounts": [{"account": "account-1", "counts": dict(
            (k, v) for k, v in baseline.items() if k != "binary_match_groups"
        )}],
        "cases": [
            {"case": f"case-{index:03d}", "category": category,
             "artifacts": count, "decision": "manual_review_only"}
            for index, (category, count) in enumerate(case_rows, start=1)
        ],
    }


def test_one_or_multiple_cases_have_only_approved_manual_check_codes():
    report = _report(cases=[
        ("confirmed_binary_match", 2),
        ("same_product_different_bytes", 2),
        ("active_entitlement_without_verified_artifact", 1),
        ("stored_hash_mismatch", 1),
    ])
    plan = kobo_manual_reconciliation_plan(report)
    assert plan["schema"] == "kobo.local.reconciliation.manual-plan/1"
    assert plan["scope"] == "single_account"
    assert plan["case_count"] == 4
    assert [c["case"] for c in plan["cases"]] == [
        "case-001", "case-002", "case-003", "case-004",
    ]
    assert plan["review_status"] == "manual_review_required"
    assert plan["evidence_complete"] is True
    for row in plan["cases"]:
        assert row["status"] == "manual_review_required"
        assert row["approved_actions"] == []
        assert "retain_all_originals" in row["checks"]
    assert plan["execution_steps"] == []
    assert plan["write_authorized"] is False
    assert plan["delete_authorized"] is False
    assert plan["download_authorized"] is False
    assert plan["automatic_reconciliation_authorized"] is False
    assert plan["retention_policy"] == "preserve_original_files"


def test_no_cases_does_not_imply_delete_or_reconcile_authorization():
    plan = kobo_manual_reconciliation_plan(_report())
    assert plan["review_status"] == "no_review_case_reported"
    assert plan["case_count"] == 0
    assert plan["delete_authorized"] is False
    assert plan["write_authorized"] is False


def test_unverified_ledger_without_case_yields_explicit_manual_blockers():
    plan = kobo_manual_reconciliation_plan(_report(counts={
        "unverified_placements": 3,
        "missing_or_unlinked": 1,
        "unproven_catalogue_links": 2,
    }))
    assert plan["unresolved_evidence_codes"] == [
        "unverified_placements", "missing_or_unlinked",
        "unproven_catalogue_links",
    ]
    assert plan["review_status"] == "manual_review_required"


def test_unindexed_candidate_is_manual_only_and_never_executable():
    audit = _report(
        counts={
            "unindexed_regular_file_candidates": 3,
            "identity_flat_file_candidates": 1,
        },
        cases=[("unindexed_identity_file_candidate", 3)],
    )
    plan = kobo_manual_reconciliation_plan(audit)
    assert plan["case_count"] == 1
    assert plan["cases"][0]["category"] == "unindexed_identity_file_candidate"
    assert "verify_rights_and_product_identity_manually" in plan["cases"][0]["checks"]
    assert "unindexed_regular_file_candidates" in plan["unresolved_evidence_codes"]
    assert plan["review_status"] == "manual_review_required"
    assert plan["execution_steps"] == []
    assert plan["delete_authorized"] is False
    assert plan["write_authorized"] is False
    assert plan["download_authorized"] is False
    for invalid in (True, -1, "3", 1_000_001):
        bad = _report(counts={"unindexed_regular_file_candidates": invalid})
        with pytest.raises(ValueError, match="invalid_kobo_audit_report"):
            kobo_manual_reconciliation_plan(bad)


def test_unindexed_without_individual_case_is_not_declared_clean():
    plan = kobo_manual_reconciliation_plan(_report(counts={
        "unindexed_regular_file_candidates": 1,
    }))
    assert plan["case_count"] == 0
    assert plan["review_status"] == "manual_review_required"
    assert "unindexed_regular_file_candidates" in plan["unresolved_evidence_codes"]
    assert plan["automatic_reconciliation_authorized"] is False


def test_truncated_audit_is_never_promoted_to_complete_plan():
    plan = kobo_manual_reconciliation_plan(_report(truncated=True))
    assert plan["evidence_complete"] is False
    assert plan["review_status"] == "incomplete_audit"
    assert "audit_budget_exhausted" in plan["unresolved_evidence_codes"]
    assert not plan["automatic_reconciliation_authorized"]


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(schema="private_schema"),
    lambda r: r.update(offline=False),
    lambda r: r.update(read_only="true"),
    lambda r: r.update(dry_run=False),
    lambda r: r.update(downloads_authorized=True),
    lambda r: r.update(mutations_authorized=0),
    lambda r: r.update(truncated="false"),
    lambda r: r.update(scope=["private"]),
    lambda r: r.update(counts={"backup_rows": -1}),
    lambda r: r.update(counts={"backup_rows": True}),
    lambda r: r.update(counts={"backup_rows": 1000001}),
    lambda r: r.update(cases="secret"),
    lambda r: r.update(accounts="secret"),
    lambda r: r["accounts"][0].update(account="private-account"),
    lambda r: r.update(accounts=r["accounts"] * 4097),
    lambda r: r.update(cases=r["cases"] * 25),
])
def test_untrusted_audit_shapes_fail_closed(mutate):
    report = _report(cases=[("confirmed_binary_match", 2)])
    mutate(report)
    with pytest.raises(ValueError, match="invalid_kobo_audit_report"):
        kobo_manual_reconciliation_plan(report)


@pytest.mark.parametrize("category", [
    "../PRIVATE/path.epub", "file:///PRIVATE", "unknown_new_category",
    "", None, ["confirmed_binary_match"],
])
def test_unknown_or_malicious_categories_are_not_reflected(category):
    report = _report(cases=[("confirmed_binary_match", 1)])
    report["cases"][0]["category"] = category
    with pytest.raises(ValueError, match="invalid_kobo_audit_report"):
        kobo_manual_reconciliation_plan(report)


def test_case_codes_are_ephemeral_and_not_arbitrary_storage_references():
    report = _report(cases=[("confirmed_binary_match", 2)])
    report["cases"][0]["case"] = "case-secret-book"
    with pytest.raises(ValueError, match="invalid_kobo_audit_report"):
        kobo_manual_reconciliation_plan(report)


def test_manual_planning_does_not_mutate_inputs_or_infer_identity():
    report = _report(cases=[("same_product_different_bytes", 2)])
    before = copy.deepcopy(report)
    result = kobo_manual_reconciliation_plan(report)
    assert report == before
    assert "account-1" not in json.dumps(result)
    assert "private" not in json.dumps(result)
    assert result["account_count"] == 1


def test_real_kobo_sha_audit_plan_preserves_all_files_and_reports_no_secrets(
    tmp_path, monkeypatch,
):
    service, session = _service(tmp_path, [])
    folder = service._kobo_backup_target_dir("default")
    first = folder / "private-a.epub"
    second = folder / "private-b.epub"
    _write_epub(first)
    second.write_bytes(first.read_bytes())
    _backup(service, "private-product-a", first)
    _backup(service, "private-product-b", second)
    monkeypatch.setattr(session, "backup_manifest", lambda: pytest.fail("no provider"))
    _no_mutation(service, monkeypatch)
    try:
        from cifereca.core.kobo_local_audit import kobo_local_reconciliation_audit
        audit = kobo_local_reconciliation_audit(service)
        plan = kobo_manual_reconciliation_plan(audit)
        assert audit["counts"]["binary_match_groups"] == 1
        assert any(x["category"] == "confirmed_binary_match" for x in plan["cases"])
        assert plan["execution_steps"] == []
        assert first.is_file() and second.is_file()
        dumped = json.dumps(plan, ensure_ascii=False)
        for secret in ("private-", "vault-1", "default", str(tmp_path)):
            assert secret not in dumped
    finally:
        service.close()


def test_cli_exposes_opt_in_plan_without_enabling_sync_or_implicit_download():
    source = Path(__file__).resolve().parents[1] / "src/cifereca/cli.py"
    code = source.read_text(encoding="utf-8")
    assert 'kobo_audit.add_argument(\n        "--plan"' in code
    assert "if a.plan:" in code
    assert "kobo_manual_reconciliation_plan(result)" in code
    assert '"kobo-audit"' in code.split("local_adept_commands =", 1)[1]
    assert "service.connect_configured()" in code  # bypassed for kobo-audit


def test_cli_plan_outputs_only_redacted_report_and_does_not_connect(
    capsys, monkeypatch,
):
    from unittest.mock import Mock
    from cifereca import cli
    import cifereca.core.kobo_local_audit as audit_module

    report = _report(cases=[("confirmed_binary_match", 2)])
    service = NS(connect_configured=Mock(
        side_effect=AssertionError("offline command must not connect"),
    ))
    core = NS(library=service, close=Mock())
    monkeypatch.setattr(cli.CiferecaCore, "open", Mock(return_value=core))
    calls = []
    def audit(_service, *, credential_id):
        assert _service is service
        calls.append(credential_id)
        return report
    monkeypatch.setattr(audit_module, "kobo_local_reconciliation_audit", audit)

    assert cli.main(["kobo-audit", "--plan", "--credential", "private-vault"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert set(result) == {"audit", "plan"}
    assert result["plan"]["case_count"] == 1
    assert result["plan"]["delete_authorized"] is False
    assert "private-vault" not in json.dumps(result)
    service.connect_configured.assert_not_called()
    core.close.assert_called_once()
    assert calls == ["private-vault"]

    core.close.reset_mock()
    assert cli.main(["kobo-audit"]) == 0
    assert json.loads(capsys.readouterr().out) == report
    service.connect_configured.assert_not_called()
    core.close.assert_called_once()
    assert calls == ["private-vault", None]
