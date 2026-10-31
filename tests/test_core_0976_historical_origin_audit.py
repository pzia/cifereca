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


def test_legacy_entitlement_id_collision_does_not_hide_either_exact_paris_origin():
    # Two separate Paris rights may contain the same local entitlement_id.
    # The lookup must compare the recorded source triple, not whichever row
    # happened to be visited last in the right enumeration.
    rights = [
        _right("alice", "paris-a", entitlement="shared-local-id"),
        _right("bob", "paris-b", entitlement="shared-local-id"),
    ]
    acquisitions = [
        _acq("alice", "paris-a", "hb", entitlement="shared-local-id"),
        _acq("bob", "paris-b", "magali", entitlement="shared-local-id"),
    ]
    for row in acquisitions:
        row["metadata"].pop("provider_loan_id")
    before = deepcopy((rights, acquisitions))
    for ordered in (rights, list(reversed(rights))):
        result = _audit(ordered, acquisitions)
        assert len(result["origins"]) == 2
        assert {r["recorded_adept_profile_id"] for r in result["origins"]} == {
            "hb", "magali",
        }
        assert result["profile_disagreement"] is True
        assert result["shared_visibility"] is True
        assert result["status"] == "recorded"
    assert (rights, acquisitions) == before


def test_legacy_identical_entitlement_id_cannot_cross_attest_a_third_account():
    rights = [
        _right("alice", "paris-a", entitlement="shared-local-id"),
        _right("bob", "paris-b", entitlement="shared-local-id"),
    ]
    acquisitions = [
        _acq("alice", "paris-a", "hb", entitlement="shared-local-id"),
        _acq("bob", "paris-b", "magali", entitlement="shared-local-id"),
        _acq("charlie", "paris-c", "foreign", entitlement="shared-local-id"),
    ]
    for row in acquisitions:
        row["metadata"].pop("provider_loan_id")
    result = _audit(rights, acquisitions)
    assert len(result["origins"]) == 2
    assert "foreign" not in {
        origin["recorded_adept_profile_id"] for origin in result["origins"]
    }
    assert result["status"] == "recorded"
    assert result["profile_disagreement"]


def test_origin_resolution_does_not_erase_disagreement_with_one_attested_profile():
    rights = [_right("alice", "paris-a"), _right("bob", "paris-b")]
    origins = [_acq("alice", "paris-a", "hb", state="attested"),
               _acq("bob", "paris-b", "magali", state="recorded")]
    before = deepcopy((rights, origins))
    result = _audit(rights, origins)
    assert result["status"] == "inventory_attested"  # legacy strongest evidence
    assert result["origin_resolution"] == "conflicting_records"
    assert result["profile_disagreement"] is True
    assert [r["evidence"] for r in result["origins"]].count("inventory_attested") == 1
    assert (rights, origins) == before


def test_origin_resolution_distinguishes_recorded_from_attested_and_unknown():
    rights = [_right("alice", "paris-a")]
    assert _audit(rights, [])["origin_resolution"] == "unknown"
    assert _audit(rights, [_acq("alice", "paris-a", "hb")])[
        "origin_resolution"] == "single_recorded"
    assert _audit(rights, [_acq("alice", "paris-a", "hb", state="attested")])[
        "origin_resolution"] == "single_inventory_attested"
    unknown = _acq("alice", "paris-a", None)
    assert _audit(rights, [unknown])["origin_resolution"] == "unknown"
    mixed = [_acq("alice", "paris-a", "hb", state="attested"),
             _acq("bob", "paris-b", "hb", state="recorded")]
    assert _audit(rights, mixed)["origin_resolution"] == "single_recorded"
    assert _audit([], [], loan="", item="book")["origin_resolution"] == "unknown"


def test_two_adobe_loans_with_one_profile_are_not_one_return_origin():
    rights = [_right("alice", "paris-a"), _right("bob", "paris-b")]
    first = _acq("alice", "paris-a", "hb", state="attested")
    second = _acq("bob", "paris-b", "hb", state="attested")
    first["metadata"]["adept_binding"]["adept_loan_id"] = "PRIVATE-LICENCE-A"
    second["metadata"]["adept_binding"]["adept_loan_id"] = "PRIVATE-LICENCE-B"
    before = deepcopy((rights, [first, second]))
    report = _audit(rights, [first, second])
    assert report["status"] == "inventory_attested"
    assert report["profile_disagreement"] is False
    assert report["adept_loan_reference_disagreement"] is True
    assert report["origin_resolution"] == "conflicting_records"
    assert len({o["recorded_adept_loan_ref"] for o in report["origins"]}) == 2
    assert "PRIVATE-LICENCE" not in str(report)
    assert (rights, [first, second]) == before


def test_same_adobe_loan_and_profile_are_consistent_only_with_inventory_evidence():
    first = _acq("alice", "paris-a", "hb", state="attested")
    second = _acq("bob", "paris-b", "hb", state="attested")
    report = _audit([], [first, second])
    assert report["adept_loan_reference_disagreement"] is False
    assert report["origin_resolution"] == "single_inventory_attested"
    second["metadata"]["adept_binding"]["inventory_status"] = "unknown"
    report = _audit([], [first, second])
    assert report["adept_loan_reference_disagreement"] is False
    assert report["origin_resolution"] == "single_recorded"


def test_missing_adept_profile_does_not_inherit_attestation_from_other_record():
    first = _acq("alice", "paris-a", "hb", state="attested")
    second = _acq("bob", "paris-b", None)
    report = _audit([], [first, second])
    assert report["status"] == "inventory_attested"
    assert report["profile_disagreement"] is False
    assert report["origin_resolution"] == "unknown"
    assert report["adept_loan_reference_disagreement"] is False
    assert _audit([], [], loan="", item="book")[
        "adept_loan_reference_disagreement"] is False

def test_non_paris_origin_audit_preserves_closed_report_schema(tmp_path):
    from dataclasses import replace
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        # An unrelated provider cannot be evidence for a Paris transaction,
        # and its refusal report must have the same safe keys as a Paris audit.
        foreign_loan = replace(loan, source_provider="kobo")
        service.storage.entitlements = lambda **_: (_ for _ in ()).throw(
            AssertionError("must not inspect rights for an unsupported provider")
        )
        service.storage.acquisitions = lambda **_: (_ for _ in ()).throw(
            AssertionError("must not inspect acquisitions for an unsupported provider")
        )
        report = service.audit_paris_adept_loan_origins(foreign_loan)
        assert report == {
            "status": "unsupported_source", "shared_visibility": False,
            "profile_disagreement": False,
            "adept_loan_reference_disagreement": False,
            "adobe_loan_state_disagreement": False,
            "origin_resolution": "unknown", "sources": [], "origins": [],
        }
    finally:
        service.close()


def test_historical_import_destination_is_not_a_second_paris_source():
    rights = [_right("alice", "paris-a")]
    acquisition = _acq("bob", "paris-import", "hb", state="attested")
    before = deepcopy((rights, [acquisition]))
    report = _audit(rights, [acquisition])
    assert len(report["sources"]) == 1
    assert report["sources"][0]["library_identity_id"] == "alice"
    assert report["shared_visibility"] is False
    assert len(report["origins"]) == 1
    assert report["origins"][0]["library_identity_id"] == "bob"
    assert report["origins"][0]["source_visibility_evidence"] == "acquisition_only"
    assert report["origin_resolution"] == "single_inventory_attested"
    assert (rights, [acquisition]) == before


def test_visible_paris_accounts_are_counted_independently_of_library_destination():
    rights = [_right("alice", "paris-a"), _right("alice", "paris-b")]
    acq = _acq("alice", "paris-a", "hb")
    report = _audit(rights, [acq])
    assert len(report["sources"]) == 2
    assert report["shared_visibility"] is True
    assert report["origins"][0]["source_visibility_evidence"] == "persisted_entitlement"


def test_duplicate_library_rows_for_same_paris_connection_are_not_two_sources():
    rights = [_right("alice", "paris-a"), _right("bob", "paris-a")]
    report = _audit(rights, [])
    assert len(report["sources"]) == 2  # two local traces, one Paris account
    assert report["shared_visibility"] is False
    assert report["origins"] == []


def test_acquisition_only_audit_keeps_origin_without_claiming_provider_exposure():
    origin = _acq("alice", "paris-import", "hb", state="attested")
    report = _audit([], [origin])
    assert report["sources"] == []
    assert report["shared_visibility"] is False
    assert report["origins"][0]["source_visibility_evidence"] == "acquisition_only"
    assert report["status"] == "inventory_attested"


def test_missing_adobe_loan_reference_does_not_inherit_other_record_evidence():
    first = _acq("alice", "paris-a", "hb", state="attested")
    second = _acq("bob", "paris-b", "hb")
    second["metadata"]["adept_binding"].pop("adept_loan_id")
    before = deepcopy([first, second])
    report = _audit([], [first, second])
    assert report["status"] == "inventory_attested"
    assert report["profile_disagreement"] is False
    assert report["adept_loan_reference_disagreement"] is False
    assert report["origin_resolution"] == "unknown"
    assert {o["recorded_adept_loan_ref"] for o in report["origins"]} == {
        None, next(o["recorded_adept_loan_ref"] for o in report["origins"]
                   if o["recorded_adept_loan_ref"])
    }
    assert [first, second] == before


def test_single_recorded_profile_without_adobe_loan_is_not_a_return_origin():
    origin = _acq("alice", "paris-a", "hb")
    origin["metadata"]["adept_binding"]["adept_loan_id"] = ""
    report = _audit([], [origin])
    assert report["status"] == "recorded"
    assert report["origins"][0]["recorded_adept_profile_id"] == "hb"
    assert report["origins"][0]["recorded_adept_loan_ref"] is None
    assert report["origin_resolution"] == "unknown"


def test_whitespace_or_nonstring_adobe_loan_reference_is_unknown():
    for bad_reference in (" ", "  \t  ", 0, None):
        origin = _acq("alice", "paris-a", "hb")
        origin["metadata"]["adept_binding"]["adept_loan_id"] = bad_reference
        report = _audit([], [origin])
        assert report["status"] == "recorded"
        assert report["origin_resolution"] == "unknown"
        assert report["origins"][0]["recorded_adept_loan_ref"] is None


def test_missing_reference_is_unknown_regardless_of_record_order():
    attested = _acq("alice", "paris-a", "hb", state="attested")
    partial = _acq("bob", "paris-b", "hb", state="recorded")
    partial["metadata"]["adept_binding"].pop("adept_loan_id")
    for rows in ([attested, partial], [partial, attested]):
        report = _audit([], rows)
        assert report["origin_resolution"] == "unknown"
        assert report["status"] == "inventory_attested"
        assert report["profile_disagreement"] is False


def test_historical_adobe_loan_state_requires_matching_inventory_attestation():
    # A persisted Adobe return is not the same as the Paris loan state or a
    # live remote check. Only the existing strict inventory contract attests it.
    for adobe_state in ("active", "returned", "expired"):
        row = _acq("alice", "paris-a", "adept-historical", state="attested")
        binding = row["metadata"]["adept_binding"]
        binding["loan_state"] = adobe_state
        if adobe_state == "returned":
            binding["token_present"] = False
            binding["return_verification"] = "confirmed"
        elif adobe_state == "expired":
            binding["expired"] = True
        result = _audit([], [row])
        origin, = result["origins"]
        assert origin["evidence"] == "inventory_attested"
        assert origin["historic_adobe_loan_state"] == adobe_state
        # This field makes no mutation and cannot attest the Paris state.
        assert "paris_loan_state" not in origin


def test_unverified_historical_return_flag_never_claims_adobe_return():
    row = _acq("alice", "paris-a", "adept-historical", state="recorded")
    binding = row["metadata"]["adept_binding"]
    binding["loan_state"] = "returned"
    binding["token_present"] = False
    binding["return_verification"] = "confirmed"
    result = _audit([], [row])
    origin, = result["origins"]
    assert origin["evidence"] == "recorded"
    assert origin["historic_adobe_loan_state"] == "unknown"
    assert result["origin_resolution"] == "single_recorded"
    # A hostile/invalid enum may never leak into the closed report.
    binding["loan_state"] = "PRIVATE-ACCOUNT-STATE"
    second = _audit([], [row])
    assert second["origins"][0]["historic_adobe_loan_state"] == "unknown"
    assert "PRIVATE-ACCOUNT-STATE" not in repr(second)


def test_same_adobe_loan_with_conflicting_attested_return_states_is_not_resolved():
    first = _acq("alice", "paris-a", "adept-historical", state="attested")
    second = _acq("bob", "paris-b", "adept-historical", state="attested")
    origin = second["metadata"]["adept_binding"]
    origin["loan_state"] = "returned"
    origin["token_present"] = False
    origin["return_verification"] = "confirmed"
    before = deepcopy((first, second))
    for ordered in ([first, second], [second, first]):
        report = _audit([], ordered)
        assert report["status"] == "inventory_attested"  # strongest evidence only
        assert report["profile_disagreement"] is False
        assert report["adept_loan_reference_disagreement"] is False
        assert report["adobe_loan_state_disagreement"] is True
        assert report["origin_resolution"] == "conflicting_records"
        assert {r["historic_adobe_loan_state"] for r in report["origins"]} == {
            "active", "returned",
        }
        assert "adept-lic" not in str(report)
    assert (first, second) == before


def test_unattested_historical_return_flag_cannot_create_state_conflict():
    attested = _acq("alice", "paris-a", "adept-historical", state="attested")
    unverified = _acq("bob", "paris-b", "adept-historical", state="recorded")
    binding = unverified["metadata"]["adept_binding"]
    binding["loan_state"] = "returned"
    binding["token_present"] = False
    binding["return_verification"] = "confirmed"
    report = _audit([], [attested, unverified])
    assert report["adobe_loan_state_disagreement"] is False
    assert report["origin_resolution"] == "single_recorded"
    assert {row["historic_adobe_loan_state"] for row in report["origins"]} == {
        "active", "unknown",
    }


def test_empty_audit_exposes_consistent_closed_disagreement_schema():
    report = _audit([], [], loan="", item="book")
    assert report["origin_resolution"] == "unknown"
    assert report["adobe_loan_state_disagreement"] is False
    assert report["sources"] == report["origins"] == []


def test_nontext_historical_provider_loan_id_never_attests_exact_transaction():
    # The string representation of malformed JSON must not accidentally
    # compare equal to a real Paris loan ID or create Adobe return evidence.
    for raw in (42, True, {"loan": "PRIVATE-TOKEN"}, ["PRIVATE-LIST"]):
        legacy = _acq("alice", "paris-a", "hb", loan="ignored", state="attested")
        legacy["metadata"]["provider_loan_id"] = raw
        before = deepcopy(legacy)
        report = _audit([], [legacy], loan=str(raw))
        assert report["origins"] == []
        assert report["status"] == "unknown"
        assert report["origin_resolution"] == "unknown"
        assert "PRIVATE-" not in repr(report)
        assert legacy == before
    # A genuinely exact textual loan ID still remains valid evidence.
    accepted = _acq("alice", "paris-a", "hb", loan="42", state="attested")
    assert _audit([], [accepted], loan="42")["status"] == "inventory_attested"


def test_nontext_item_or_entitlement_id_is_never_an_exact_paris_link():
    bad_item = _acq("alice", "paris-a", "hb", state="attested")
    bad_item["item_id"] = {"item": "PRIVATE-TOKEN"}
    report = _audit([], [bad_item], item=str(bad_item["item_id"]))
    assert report["origins"] == []
    assert "PRIVATE-" not in repr(report)

    entitlement = _right("alice", "paris-a", entitlement="legacy-right")
    entitlement["loan_id"] = 42
    assert _audit([entitlement], [], loan="42")["sources"] == []

    legacy = _acq("alice", "paris-a", "hb", state="attested",
                  entitlement="legacy-right")
    legacy["metadata"].pop("provider_loan_id")
    entitlement["loan_id"] = "shared"
    entitlement["entitlement_id"] = 42
    legacy["entitlement_id"] = 42
    assert _audit([entitlement], [legacy])["origins"] == []
    entitlement["entitlement_id"] = "42"
    legacy["entitlement_id"] = "42"
    assert _audit([entitlement], [legacy])["origin_resolution"] == (
        "single_inventory_attested"
    )


def test_invalid_nontext_scope_cannot_call_artifact_inspector():
    def forbidden(_):
        raise AssertionError("invalid scope must not inspect files")

    for loan, item in ((42, "book"), ("shared", {"id": "PRIVATE-TOKEN"}),
                       ("shared", "\x00book")):
        result = inspect_paris_adept_origins(
            loan_id=loan, item_id=item,
            entitlements=[_right("alice", "paris-a")],
            acquisitions=[_acq("alice", "paris-a", "hb", state="attested")],
            artifact_inspector=forbidden,
        )
        assert result["status"] == "unknown"
        assert result["origins"] == result["sources"] == []
        assert result["origin_resolution"] == "unknown"
        assert "PRIVATE-TOKEN" not in repr(result)


def test_same_paris_borrower_with_distinct_library_destination_is_an_indication():
    right = _right("alice", "paris-a")
    completed = _acq("bob", "paris-a", "adept-historical", state="attested")
    before = deepcopy((right, completed))
    report = _audit([right], [completed])
    source, = report["sources"]
    origin, = report["origins"]
    assert source["library_identity_id"] == "alice"
    assert origin["library_identity_id"] == "bob"
    assert source["source_ref"] != origin["source_ref"]
    assert source["paris_borrower_ref"] == origin["paris_borrower_ref"]
    assert origin["source_visibility_evidence"] == "acquisition_only"
    assert report["shared_visibility"] is False
    assert (right, completed) == before


def test_borrower_only_reference_rejects_malformed_historical_account_coordinates():
    right = _right("alice", "paris-a")
    right["account_id"] = {"not": "a-paris-account"}
    origin = _acq("bob", "paris-a", "adept-historical", state="attested")
    origin["account_id"] = ["primary"]
    report = _audit([right], [origin])
    source, = report["sources"]
    historical, = report["origins"]
    assert source["paris_borrower_ref"] is None
    assert historical["paris_borrower_ref"] is None
    assert source["source_ref"] != historical["source_ref"]
    assert historical["source_visibility_evidence"] == "acquisition_only"


@pytest.mark.parametrize("field", ["identity_id", "credential_id", "account_id"])
@pytest.mark.parametrize("invalid", [123, True, ["PRIVATE-TOKEN"], {"token": "PRIVATE-TOKEN"}, "bad\x00id"])
def test_malformed_paris_source_coordinate_is_not_entitlement_or_adobe_proof(
    field, invalid,
):
    right = _right("alice", "paris-a")
    acquisition = _acq("alice", "paris-a", "adept-historical", state="attested")
    right[field] = invalid
    acquisition[field] = invalid
    before = deepcopy((right, acquisition))
    report = _audit([right], [acquisition])
    assert report["status"] == "unknown"
    assert report["sources"] == report["origins"] == []
    assert "PRIVATE-TOKEN" not in repr(report)
    assert (right, acquisition) == before


@pytest.mark.parametrize("field", ["identity_id", "credential_id", "account_id"])
def test_malformed_recorded_acquisition_scope_cannot_borrow_textual_paris_right(
    field,
):
    right = _right("alice", "paris-a")
    right[field] = "42"
    acquisition = _acq("alice", "paris-a", "adept-historical", state="attested")
    acquisition[field] = 42
    report = _audit([right], [acquisition])
    assert len(report["sources"]) == 1
    assert report["origins"] == []
    assert report["status"] == "unknown"


def test_missing_legacy_source_coordinates_keep_compatible_blank_scope():
    # A genuinely absent legacy coordinate is not the same as a malformed
    # present JSON value. Older receipts may have used empty library fields.
    right = _right("alice", "paris-a", entitlement="legacy-entitlement")
    acquisition = _acq(
        "alice", "paris-a", "adept-historical", state="attested",
        entitlement="legacy-entitlement",
    )
    acquisition["metadata"].pop("provider_loan_id")
    right.pop("account_id")
    acquisition.pop("account_id")
    result = _audit([right], [acquisition])
    assert len(result["sources"]) == 1
    assert result["origins"][0]["source_visibility_evidence"] == "persisted_entitlement"
    assert result["origin_resolution"] == "single_inventory_attested"
    assert result["origins"][0]["paris_borrower_ref"] is None
