"""P0 #976 / historical #987: evidence audit is safe across Paris sources."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from cifereca.core.adept_origin_audit import inspect_paris_adept_origins
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


def _right(identity, credential, *, loan="shared", item="book", entitlement=None):
    return {
        "provider_id": "paris", "identity_id": identity,
        "credential_id": credential, "account_id": "primary",
        "loan_id": loan, "item_id": item,
        "entitlement_id": entitlement or "right-" + credential,
    }


def _acq(identity, credential, profile, *, loan="shared", item="book",
         state="recorded", entitlement=None, path=None, strategy="loan_auto"):
    binding = {
        "adept_profile_id": profile, "adept_profile_name": "cli-" + str(profile),
        "adept_loan_id": "adept-lic",
        "loan_state": "active", "token_present": True,
        "return_available": True,
    }
    if state == "attested":
        binding.update(
            inventory_status="resolved", inventory_checked_at=123456,
            binding_source="public_inventory", expired=False,
            return_verification="active_confirmed",
        )
    metadata = {"provider_loan_id": loan, "adept_binding": binding}
    if path is not None:
        metadata["path"] = str(path)
    return {
        "acquisition_id": "acq-" + credential, "provider_id": "paris",
        "identity_id": identity, "credential_id": credential,
        "account_id": "primary", "item_id": item, "strategy": strategy,
        "executor": "kobo-adept", "status": "complete",
        "entitlement_id": entitlement, "metadata": metadata,
    }


def _audit(entitlements, acquisitions, *, loan="shared", item="book", inspector=None):
    return inspect_paris_adept_origins(
        loan_id=loan, item_id=item,
        entitlements=entitlements, acquisitions=acquisitions,
        artifact_inspector=inspector or (lambda _: SimpleNamespace(valid=True, protected=False)),
    )


def test_two_paris_connections_one_historical_profile_is_not_a_wrong_activation():
    rows = [_right("alice", "paris-a"), _right("bob", "paris-b")]
    origins = [_acq("alice", "paris-a", "shared-hb", state="attested")]
    before = deepcopy((rows, origins))
    result = _audit(rows, origins)
    assert result["status"] == "inventory_attested"
    assert result["shared_visibility"] is True
    assert result["profile_disagreement"] is False
    assert len(result["sources"]) == 2
    assert result["origins"][0]["recorded_adept_profile_id"] == "shared-hb"
    assert result["origins"][0]["evidence"] == "inventory_attested"
    assert (rows, origins) == before


def test_profiles_differing_only_in_records_are_a_clue_not_server_proof():
    r = _audit(
        [_right("alice", "paris-a"), _right("bob", "paris-b")],
        [_acq("alice", "paris-a", "hb"),
         _acq("bob", "paris-b", "magali")],
    )
    assert r["status"] == "recorded"
    assert r["profile_disagreement"] is True
    assert all(o["evidence"] == "recorded" for o in r["origins"])


def test_no_title_item_or_different_loan_false_matches():
    rights = [_right("alice", "paris-a", loan="shared", item="book")]
    evidence = [_acq("alice", "paris-a", "hb", loan="other", item="book"),
                _acq("alice", "paris-c", "hb", loan="shared", item="other")]
    r = _audit(rights, evidence)
    assert r["status"] == "unknown"
    assert r["origins"] == []


def test_legacy_missing_provider_loan_id_requires_exact_right_and_source():
    right = _right("alice", "paris-a", entitlement="unique-right")
    matching = _acq("alice", "paris-a", "hb", entitlement="unique-right")
    matching["metadata"].pop("provider_loan_id")
    unrelated = _acq("bob", "paris-b", "magali", entitlement="unique-right")
    unrelated["metadata"].pop("provider_loan_id")
    r = _audit([right], [matching, unrelated])
    assert len(r["origins"]) == 1
    assert r["origins"][0]["recorded_adept_profile_id"] == "hb"


def test_missing_artifact_is_not_license_proof(tmp_path):
    missing = tmp_path / "missing.epub"
    orig = _acq("alice", "paris-a", "hb", path=missing, state="attested")
    result = _audit([], [orig], inspector=lambda _: (_ for _ in ()).throw(
        AssertionError("missing file must never be inspected")
    ))
    assert result["origins"][0]["artifact_state"] == "missing"
    assert result["status"] == "inventory_attested"


def test_protected_file_is_not_usable_and_never_overwritten(tmp_path):
    artifact = tmp_path / "historical.epub"
    artifact.write_bytes(b"old protected")
    evidence = _acq("alice", "paris-a", "hb", path=artifact)
    r = _audit([], [evidence], inspector=lambda _: SimpleNamespace(valid=True, protected=True))
    assert r["origins"][0]["artifact_state"] == "protected"
    assert artifact.read_bytes() == b"old protected"


def test_no_source_secrets_or_raw_artifact_paths_in_output(tmp_path):
    secret = "private-credential-4711"
    artifact = tmp_path / "private-loan.epub"
    artifact.write_bytes(b"book")
    result = _audit([_right("alice", secret)],
                    [_acq("alice", secret, "hb", path=artifact)])
    rendered = str(result)
    assert secret not in rendered
    assert str(artifact) not in rendered
    assert "private-loan" not in rendered
    assert result["origins"][0]["artifact_state"] == "usable"


def test_non_loan_auto_or_incomplete_adept_is_never_origin_proof():
    rows = [_acq("alice", "paris-a", "hb", strategy="manual")]
    rows += [{**_acq("alice", "paris-b", "hb"), "status": "attention_required"}]
    result = _audit([], rows)
    assert result["origins"] == [] and result["status"] == "unknown"


def test_service_facade_is_read_only_and_does_not_call_kobo_or_paris(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service.kobo_profiles = lambda: (_ for _ in ()).throw(
            AssertionError("do not query ADEPT profiles")
        )
        service.acquire_digital_loan = lambda *_: (_ for _ in ()).throw(
            AssertionError("do not acquire an ACSM")
        )
        service._persist_active_loans("alice", [loan])
        result = service.audit_paris_adept_loan_origins(loan)
        assert result["status"] == "unknown"
        assert len(result["sources"]) == 1
        assert result["origins"] == []
        assert service.storage.acquisitions(provider_id="paris") == []
    finally:
        service.close()


def test_malformed_artifact_inspection_is_unknown_not_usable(tmp_path):
    artifact = tmp_path / "historical.epub"
    artifact.write_bytes(b"private fixture")
    row = _acq("alice", "paris-a", "hb", path=artifact, state="attested")
    from cifereca.core.adept_origin_audit import _artifact_state
    for response in (
        None,
        SimpleNamespace(valid=True),
        SimpleNamespace(valid=True, protected=None),
        SimpleNamespace(valid=1, protected=False),
        SimpleNamespace(valid=False, protected=None),
    ):
        assert _artifact_state(row["metadata"], lambda _, r=response: r) == "inspection_unavailable"
    assert _artifact_state(row["metadata"], lambda _: SimpleNamespace(
        valid=False, protected=False)) == "invalid"
    assert _artifact_state(row["metadata"], lambda _: SimpleNamespace(
        valid=True, protected=True)) == "protected"
    assert _artifact_state(row["metadata"], lambda _: SimpleNamespace(
        valid=True, protected=False)) == "usable"
    assert artifact.read_bytes() == b"private fixture"


def test_broken_inspector_does_not_abort_historical_origin_report(tmp_path):
    artifact = tmp_path / "historical.epub"
    artifact.write_bytes(b"private fixture")
    rows = [_acq("alice", "paris-a", "hb", path=artifact, state="attested"),
            _acq("bob", "paris-b", "magali", path=artifact)]
    calls = []
    def broken_inspector(path):
        calls.append(path)
        raise RuntimeError("PRIVATE_ACCOUNT/PRIVATE_LOAN/PRIVATE_PATH")
    report = _audit([_right("alice", "paris-a"), _right("bob", "paris-b")],
                    rows, inspector=broken_inspector)
    assert len(calls) == 2
    assert report["status"] == "inventory_attested"
    assert report["shared_visibility"] is True
    assert report["profile_disagreement"] is True
    assert [o["artifact_state"] for o in report["origins"]] == [
        "inspection_unavailable", "inspection_unavailable"]
    assert "PRIVATE_" not in str(report)
    assert artifact.read_bytes() == b"private fixture"
