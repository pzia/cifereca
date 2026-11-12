"""P0 #5048: inert installed-provider manual-borrow ABI, no supplier I/O."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.providers.base import (
    ManualBorrowPolicy, ProviderCapabilities, ProviderDescriptor,
)
from cifereca.core.manual_borrow_contract import (
    resolved_manual_borrow_policy, manual_borrow_action_available,
    manual_borrow_action,
)
from tests.test_core_p0_2055_manual_dispatch import setup as bnf_setup
from tests.test_paris_manual_borrow_dispatch import setup as paris_setup


@pytest.mark.parametrize("fields", [
    {"docbase": ""}, {"docbase": "DILI COM"},
    {"docbase": "DILICOM", "schema": "cifereca.manual-borrow/2"},
    {"docbase": "DILICOM", "primary_borrower_id": "primary"},
    {"docbase": "DILICOM", "primary_borrower_id": "",
     "verification": "active_only"},
    {"docbase": "DILICOM", "verification": "provider_observed"},
    {"docbase": "DILICOM", "source_selection_key": "bad/key"},
    {"docbase": "DILICOM", "source_selection_key": 17},
    {"docbase": "DILICOM", "observed_primary_account_alias": "primary"},
    {"docbase": "DILICOM", "primary_borrower_id": "",
     "verification": "provider_observed", "observed_primary_account_alias": "bad/path"},
])
def test_manual_borrow_policy_rejects_malformed_or_unsupported_abi(fields):
    with pytest.raises(ValueError, match="invalid_manual_borrow_policy"):
        ManualBorrowPolicy(**fields)


def test_manual_borrow_policy_defaults_are_inert():
    d = ProviderDescriptor(
        id="synthetic", display_name="Synthetic", description="fixture",
        state="ready", capabilities=ProviderCapabilities(borrow=True),
    )
    assert d.manual_borrow_policy is None
    assert ManualBorrowPolicy("BNF_PRET").verification == "active_only"
    assert ManualBorrowPolicy(
        "DILICOM", primary_borrower_id="", verification="provider_observed",
    ).primary_borrower_id == ""


def test_bnf_exact_installed_contract_uses_shared_readiness(tmp_path):
    service, session, account, item = bnf_setup(tmp_path)
    try:
        policy = ManualBorrowPolicy("BNF_PRET")
        session.descriptor = replace(session.descriptor, manual_borrow_policy=policy)
        assert resolved_manual_borrow_policy(service, item) == policy
        assert manual_borrow_action_available(service, item)
        assert service.can_borrow_manual(item)

        # Notice metadata is not the opt-in source of truth.
        forged = replace(item, metadata={"manual_borrow_presentation": {
            "provider_id": "foreign", "docbase": "DILICOM",
            "record_id": item.record_id,
        }})
        assert resolved_manual_borrow_policy(service, forged) == policy
        assert not manual_borrow_action_available(
            service, replace(item, docbase="OTHER"),
        )

        # A disabled installation, rebound session or absent ABI fails closed.
        session.descriptor = replace(session.descriptor, manual_borrow_policy=None)
        assert resolved_manual_borrow_policy(service, item) is None
        assert not service.can_borrow_manual(item)
        out = service.borrow_manual(item, expected_identity_id=account.identity_id,
                                    confirmed=True, expected_account=account)
        assert out["status"] == "blocked"
        assert not session.dispatches
    finally:
        service.close()


def test_paris_declared_policy_preserves_existing_exact_source_admission(tmp_path):
    service, session, account, item, state = paris_setup(tmp_path)
    try:
        policy = ManualBorrowPolicy(
            "DILICOM", primary_borrower_id="",
            source_selection_key="primary_paris_connection_id",
            verification="provider_observed",
            observed_primary_account_alias="primary",
        )
        assert resolved_manual_borrow_policy(service, item) == policy
        # Preserve the positive exact-source test after deleting the legacy
        # service facade. The read-only diagnostic is an independent oracle.
        assert service.manual_borrow_diagnostic(item)["local_action_possible"] is True
        assert service.can_borrow_manual(item) is True
        assert not state["calls"] and state["mutations"] == 0
        assert not service.can_borrow_manual(replace(item, docbase="OTHER"))
        account.unique_id = "linked-reader"
        assert not service.can_borrow_manual(item)
        assert not state["calls"] and state["mutations"] == 0
    finally:
        service.close()


def test_manifest_and_session_mismatch_default_deny(tmp_path):
    service, session, account, item = bnf_setup(tmp_path)
    try:
        session.descriptor = replace(
            session.descriptor,
            manual_borrow_policy=ManualBorrowPolicy("BNF_PRET"),
        )
        status = service._plugin_manager.status("provider.bnf-pret")
        for change in ("disabled", "capability", "session", "descriptor"):
            old_manager = service._plugin_manager
            old_connected = session.connected
            old_descriptor = session.descriptor
            if change == "disabled":
                service._plugin_manager = NS(status=lambda _: NS(
                    state="ready", enabled=False,
                    descriptor=status.descriptor,
                ))
            elif change == "capability":
                service._plugin_manager = NS(status=lambda _: NS(
                    state="ready", enabled=True,
                    descriptor=NS(
                        id="provider.bnf-pret", type="provider.library",
                        capabilities=("sync.loans",),
                    ),
                ))
            elif change == "session":
                session.connected = False
            else:
                session.descriptor = replace(
                    session.descriptor, id="foreign",
                )
            assert resolved_manual_borrow_policy(service, item) is None
            assert not service.can_borrow_manual(item)
            service._plugin_manager = old_manager
            session.connected = old_connected
            session.descriptor = old_descriptor
    finally:
        service.close()


def test_generic_bnf_uses_only_durable_one_shot_executor(tmp_path):
    from cifereca.core.borrow_action_scope import borrow_claim_key

    service, session, account, item = bnf_setup(tmp_path)
    try:
        session.descriptor = replace(
            session.descriptor,
            manual_borrow_policy=ManualBorrowPolicy("BNF_PRET"),
        )
        first = service.borrow_manual(
            item, expected_identity_id=account.identity_id,
            confirmed=True, expected_account=account,
            expected_context_epoch=service._account_context_epoch,
        )
        assert first["status"] == "confirmed"
        assert first["provider_verified"] is True
        assert session.dispatches == [("book-1", "reader")]
        second = service.borrow_manual(
            item, expected_identity_id=account.identity_id,
            confirmed=True, expected_account=account,
            expected_context_epoch=service._account_context_epoch,
        )
        assert second["status"] == "verification_unavailable"
        assert session.dispatches == [("book-1", "reader")]
        assert service.storage.loan_borrow_state(
            borrow_claim_key(account, item),
        )["status"] == "confirmed"
    finally:
        service.close()


def test_generic_paris_uncertain_post_is_never_replayed(tmp_path):
    service, session, account, item, state = paris_setup(
        tmp_path, result="timeout-unconfirmed",
    )
    try:
        args = dict(
            expected_identity_id=account.identity_id,
            confirmed=True, expected_account=account,
            expected_context_epoch=service._account_context_epoch,
        )
        first = service.borrow_manual(item, **args)
        assert first["status"] == "verification_unavailable"
        assert state["mutations"] == 1
        second = service.borrow_manual(item, **args)
        assert second["status"] == "verification_unavailable"
        assert second["reason_code"] == "borrow_previous_attempt"
        assert state["mutations"] == 1
        assert service.storage.entitlements(provider_id="paris") == []
    finally:
        service.close()


@pytest.mark.parametrize("confirmation", [False, None, 1, "yes"])
def test_generic_action_denies_ambiguous_consent_before_supplier_io(tmp_path, confirmation):
    service, session, account, item, state = paris_setup(tmp_path)
    try:
        result = service.borrow_manual(
            item, expected_identity_id=account.identity_id,
            confirmed=confirmation, expected_account=account,
        )
        assert result["status"] == "blocked"
        assert result["reason_code"] == "borrow_confirmation_required"
        assert state["calls"] == [] and state["mutations"] == 0
    finally:
        service.close()


def test_mismatched_explicit_verification_modes_fail_before_supplier_io(tmp_path):
    from cifereca.core.manual_library_borrow import manual_borrow

    service, session, account, item = bnf_setup(tmp_path)
    try:
        result = manual_borrow(
            service, item, provider_id="bnf-pret", docbase="BNF_PRET",
            primary_id=None, verification="provider_observed",
            expected_identity_id=account.identity_id, confirmed=True,
        )
        assert result["status"] == "blocked"
        assert session.dispatches == session.reads == []
    finally:
        service.close()

    service, session, account, item, state = paris_setup(tmp_path)
    try:
        result = manual_borrow(
            service, item, provider_id="paris", docbase="DILICOM",
            primary_id="", verification="active_only",
            expected_identity_id=account.identity_id, confirmed=True,
        )
        assert result["status"] == "blocked"
        assert state["calls"] == [] and state["mutations"] == 0
    finally:
        service.close()


def test_provider_observed_account_alias_mismatch_never_confirms_or_retries(tmp_path):
    from cifereca.core.borrow_action_scope import borrow_claim_key

    service, session, account, item, state = paris_setup(tmp_path)
    try:
        # The actual synthetic Paris provider observes account "primary".
        # A conflicting installed declaration must never turn that into a
        # verified loan, even if the lender returns an otherwise valid loan.
        session.descriptor = replace(
            session.descriptor,
            manual_borrow_policy=replace(
                session.descriptor.manual_borrow_policy,
                observed_primary_account_alias="foreign_reader",
            ),
        )
        args = dict(
            expected_identity_id=account.identity_id, confirmed=True,
            expected_account=account,
            expected_context_epoch=service._account_context_epoch,
        )
        first = service.borrow_manual(item, **args)
        assert first["status"] == "verification_unavailable"
        assert first["provider_verified"] is False
        assert state["mutations"] == 1
        assert service.storage.loan_borrow_state(
            borrow_claim_key(account, item),
        )["status"] == "verification_unavailable"
        second = service.borrow_manual(item, **args)
        assert second["status"] == "verification_unavailable"
        assert second["reason_code"] == "borrow_previous_attempt"
        assert state["mutations"] == 1
    finally:
        service.close()


def test_missing_or_inconsistent_observed_account_policy_fails_before_post(tmp_path):
    service, session, account, item, state = paris_setup(tmp_path)
    try:
        original = session.descriptor
        for policy in (
            None,
            ManualBorrowPolicy("DILICOM"),
            ManualBorrowPolicy(
                "DILICOM", primary_borrower_id="",
                verification="provider_observed",
                observed_primary_account_alias="primary",
                source_selection_key="another_primary_selection",
            ),
        ):
            session.descriptor = replace(
                original, manual_borrow_policy=policy,
            )
            result = service.borrow_manual(
                item, expected_identity_id=account.identity_id,
                confirmed=True, expected_account=account,
            )
            assert result["status"] == "blocked"
            assert state["mutations"] == 0 and state["calls"] == []
        session.descriptor = original
    finally:
        service.close()
