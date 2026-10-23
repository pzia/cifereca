"""#2975: missing loans cannot be inferred from another/partial borrower list.

All sources, accounts and rights are synthetic local fixtures. No provider,
network, DRM operation or file mutation is initiated.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca.core.sync_coordination import _opening_loans_scoped
from cifereca.models import Account, Loan
from tests.test_sync_state_contract import _service


def _loan(
    name: str, *, provider: str = "paris", credential: str = "cred",
    account: str = "reader", identity: str = "default",
) -> Loan:
    return Loan(
        id=f"loan-{name}", provider="DILICOM", medium="digital",
        record_id=f"book-{name}", title=f"Synthetic {name}",
        source_provider=provider, source_credential=credential,
        source_account_id=account, identity_id=identity,
        due_at="2099-01-01T00:00:00Z",
    )


def _rights(service):
    return {
        row["loan_id"]: row
        for row in service.storage.entitlements()
        if row["entitlement_type"] == "loan"
    }


def test_unknown_empty_and_partial_inventories_never_realize(tmp_path):
    service = _service(tmp_path)
    try:
        loan = _loan("paris")
        service._persist_active_loans("default", [loan])
        for _ in range(3):
            result = service._persist_active_loans("default", [])
            assert result["returned_realized"] == 0
            result = service._persist_active_loans(
                "default", [], reconcile_missing=True,
            )
            assert result["returned_realized"] == 0
        assert _rights(service)[loan.id]["state"] == "active"
    finally:
        service.close()


def test_negative_scope_is_exact_account_provider_credential_and_identity(tmp_path):
    service = _service(tmp_path)
    try:
        a = _loan("A", account="reader-A")
        b = _loan("B", account="reader-B")
        c = _loan("C", provider="kobo", credential="kobo-cred", account="reader-A")
        other = _loan("D", identity="alex", account="reader-A")
        service._persist_active_loans("default", [a, b, c])
        service._persist_active_loans("alex", [other])
        changed = service.storage.entitlement_realize_missing_loans(
            set(), identity_id="default",
            complete_scopes={("paris", "cred", "reader-A")},
        )
        assert changed == 1
        assert _rights(service)[a.id]["state"] == "realized"
        assert all(_rights(service)[loan.id]["state"] == "active" for loan in (b, c, other))
        # Identical repeated complete evidence is idempotent.
        assert service.storage.entitlement_realize_missing_loans(
            set(), identity_id="default",
            complete_scopes={("paris", "cred", "reader-A")},
        ) == 0
        # Positive row in a complete scope must remain active.
        assert service.storage.entitlement_realize_missing_loans(
            {_rights(service)[b.id]["entitlement_id"]}, identity_id="default",
            complete_scopes={("paris", "cred", "reader-B")},
        ) == 0
        assert _rights(service)[b.id]["state"] == "active"
    finally:
        service.close()


@pytest.mark.parametrize("scope", [
    None, set(), {("paris", "cred")}, {("paris", "", "reader")},
    {("paris", "cred", None)}, {("paris", "cred", "reader"), ("kobo", "cred", None)},
])
def test_unproven_or_malformed_scopes_cannot_realize(tmp_path, scope):
    service = _service(tmp_path)
    try:
        loan = _loan("protected")
        service._persist_active_loans("default", [loan])
        assert service.storage.entitlement_realize_missing_loans(
            set(), identity_id="default", complete_scopes=scope,
        ) == 0
        assert _rights(service)[loan.id]["state"] == "active"
    finally:
        service.close()


@pytest.mark.parametrize("error", [
    {"upstream": "DILICOM", "error": "PRIVATE_PASSWORD"},
    {"upstream": "SYRACUSE", "error": "PRIVATE_PASSWORD"},
])
def test_opening_partial_paris_does_not_close_existing_right(tmp_path, monkeypatch, error):
    service = _service(tmp_path)
    try:
        loan = _loan("existing")
        service._persist_active_loans("default", [loan])
        account = Account(
            display_name="Synthetic Paris", unique_id="reader", provider_id="paris",
            credential_id="cred", identity_id="default",
        )
        service.accounts = [account]
        service._sessions[service._session_key("paris", "cred")] = SimpleNamespace(
            provider_id="paris", credential_id="cred", connected=True,
            last_loan_errors=[error],
        )
        monkeypatch.setattr(service, "_loans_for_account", lambda account, force=False: [])
        summary = service.opening_sync()
        current = next(row for row in summary["identities"] if row["identity_id"] == "default")
        assert current["status"] == "partial"
        assert current["realized"] == 0
        assert _rights(service)[loan.id]["state"] == "active"
        assert "PRIVATE_PASSWORD" not in json.dumps(summary)
    finally:
        service.close()


def test_opening_fully_empty_paris_still_requires_explicit_negative_proof(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        loan = _loan("still-uncertain")
        service._persist_active_loans("default", [loan])
        account = Account(
            display_name="Paris", unique_id="reader", provider_id="paris",
            credential_id="cred", identity_id="default",
        )
        service.accounts = [account]
        service._sessions[service._session_key("paris", "cred")] = SimpleNamespace(
            provider_id="paris", credential_id="cred", connected=True,
            last_loan_errors=[],
        )
        monkeypatch.setattr(service, "_loans_for_account", lambda account, force=False: [])
        summary = service.opening_sync()
        assert next(row for row in summary["identities"] if row["identity_id"] == "default")["realized"] == 0
        assert _rights(service)[loan.id]["state"] == "active"
    finally:
        service.close()


def test_mixed_borrower_rows_rejected_before_any_entitlement_write(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        account = Account(
            display_name="Paris", unique_id="reader", provider_id="paris",
            credential_id="cred", identity_id="default",
        )
        service.accounts = [account]
        existing = _loan("existing")
        service._persist_active_loans("default", [existing])
        wrong = _loan("spoof", account="different-reader")
        monkeypatch.setattr(service, "_loans_for_account", lambda account, force=False: [wrong])
        summary = service.opening_sync()
        assert next(row for row in summary["identities"] if row["identity_id"] == "default")["status"] == "failed"
        assert set(_rights(service)) == {existing.id}
        assert _rights(service)[existing.id]["state"] == "active"
    finally:
        service.close()


@pytest.mark.parametrize("field,value", [
    ("identity_id", "alex"),
    ("source_provider", "kobo"),
    ("source_credential", "other-credential"),
    ("source_account_id", "another-reader"),
])
def test_session_mixed_origin_is_rejected_before_normalization(
    tmp_path, monkeypatch, field, value,
):
    from dataclasses import replace
    from cifereca.providers import ProviderConfigurationError

    service = _service(tmp_path)
    try:
        account = Account(
            display_name="Paris", unique_id="reader",
            provider_id="paris", credential_id="cred", identity_id="default",
        )
        row = replace(_loan("foreign"), **{field: value})
        session = SimpleNamespace(
            capabilities=SimpleNamespace(loans=True),
            loans=lambda _account: [row],
        )
        monkeypatch.setattr(service, "_session_for_account", lambda _account: session)
        with pytest.raises(ProviderConfigurationError, match="périmètre de compte"):
            service._loans_for_account(account, force=True)
        assert getattr(row, field) == value
        assert not service.storage.entitlements()
    finally:
        service.close()


def test_a_failed_account_and_a_healthy_empty_account_never_realize_each_other(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        failed = Account(
            display_name="Paris A", unique_id="reader-A",
            provider_id="paris", credential_id="cred-A", identity_id="default",
        )
        healthy = Account(
            display_name="Paris B", unique_id="reader-B",
            provider_id="paris", credential_id="cred-B", identity_id="default",
        )
        service.accounts = [failed, healthy]
        a = _loan("failed", credential="cred-A", account="reader-A")
        b = _loan("healthy", credential="cred-B", account="reader-B")
        service._persist_active_loans("default", [a, b])

        def load(account, *, force=False):
            assert force is True
            if account is failed:
                raise RuntimeError("PRIVATE_SIGNED_ACSM_URL")
            return []

        monkeypatch.setattr(service, "_loans_for_account", load)
        result = service.opening_sync()
        state = next(x for x in result["identities"] if x["identity_id"] == "default")
        assert state["status"] == "partial"
        assert state["realized"] == 0
        assert _rights(service)[a.id]["state"] == "active"
        assert _rights(service)[b.id]["state"] == "active"
        assert "PRIVATE_SIGNED_ACSM_URL" not in json.dumps(result)
    finally:
        service.close()


def test_evidence_of_absence_never_deletes_file_artifact_placement_or_ledger(tmp_path):
    import hashlib

    service = _service(tmp_path)
    try:
        loan = _loan("kept")
        service._persist_active_loans("default", [loan])
        entitlement_id = _rights(service)[loan.id]["entitlement_id"]
        book = tmp_path / "private-synthetic.epub"
        payload = b"FAKE-EPUB-NO-DRM-OR-PRIVATE-DATA"
        book.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        artifact_id = service.storage.artifact_put({
            "artifact_id": "artifact:test", "sha256": digest,
            "size": len(payload), "media_format": "epub",
        })
        service.storage.placement_put({
            "placement_id": "placement:test", "artifact_id": artifact_id,
            "path": str(book), "identity_id": "default",
            "provider_id": "paris", "source_item_id": "book-kept",
            "state": "present",
        })
        service.storage.acquisition_put({
            "acquisition_id": "acquisition:test", "entitlement_id": entitlement_id,
            "provider_id": "paris", "credential_id": "cred", "account_id": "reader",
            "item_id": "book-kept", "status": "complete", "artifact_id": artifact_id,
            "identity_id": "default",
        })
        before_place = service.storage.placements()
        before_ledger = service.storage.acquisitions()
        before_artifact = service.storage.artifacts()

        assert service.storage.entitlement_realize_missing_loans(
            set(), identity_id="default", complete_scopes=None,
        ) == 0
        assert _rights(service)[loan.id]["state"] == "active"
        assert service.storage.entitlement_realize_missing_loans(
            set(), identity_id="default",
            complete_scopes={("paris", "cred", "reader")},
        ) == 1
        assert _rights(service)[loan.id]["state"] == "realized"
        assert _rights(service)[loan.id]["entitlement_type"] == "loan"
        assert service.storage.placements() == before_place
        assert service.storage.acquisitions() == before_ledger
        assert service.storage.artifacts() == before_artifact
        assert book.read_bytes() == payload
    finally:
        service.close()


def test_opening_scope_helper_accepts_only_exact_positive_row_origin():
    account = Account(
        display_name="Paris", unique_id="reader", provider_id="paris",
        credential_id="cred", identity_id="default",
    )
    exact = _loan("ok")
    assert _opening_loans_scoped(account, "default", [exact])
    assert _opening_loans_scoped(account, "default", [])
    assert not _opening_loans_scoped(account, "default", [_loan("wrong", identity="alex")])
    assert not _opening_loans_scoped(account, "default", [_loan("wrong", credential="other")])
    assert not _opening_loans_scoped(account, "default", [object()])
