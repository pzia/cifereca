"""P0 #977: do not replay consumed ACSM or choose another Adobe profile."""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.acquisition import Acquisition
from cifereca.config import Config
from cifereca.core.adept_retry_guard import adept_attention_code, blocked_automatic_adept_retry
from cifereca.diagnostics import Diagnostics
from cifereca.models import Loan, Account
from cifereca.plugin_api import UserFacingError
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _service(tmp_path: Path) -> LibraryService:
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set("identities", [{
        "id": "alex", "label": "Alex", "directory_name": "alex",
        "preferences": {"reading_languages": ["fr"]},
    }])
    config.set("providers.accounts", [])
    service = LibraryService(config, Storage(tmp_path / "data.sqlite3"), Diagnostics())
    service.storage.provider_connection_put({
        "identity_id": "alex", "provider_id": "paris", "credential_id": "alex-paris",
        "label": "Paris Alex",
    })
    service.accounts.append(Account(unique_id="primary", provider_id="paris", credential_id="alex-paris", identity_id="alex"))
    return service


def _loan(loan_id="loan-a", item_id="book-a") -> Loan:
    return Loan(
        id=loan_id, provider="PNB", medium="digital", record_id=item_id,
        title="Un livre", source_provider="paris", source_credential="alex-paris",
        source_account_id="primary", identity_id="alex",
        ebook_url="https://example.invalid/acsm",
        due_at="2099-01-01T00:00:00Z",
    )


def _seed(service, *, status="attention_required", executor="kobo-adept",
          loan_id="loan-a", item_id="book-a", metadata=None):
    return service.storage.acquisition_put({
        "provider_id": "paris", "credential_id": "alex-paris",
        "account_id": "primary", "identity_id": "alex", "item_id": item_id,
        "title": "Un livre", "status": status, "strategy": "loan_auto",
        "executor": executor, "error_code": "adept_error",
        "attention_code": "adept_error" if status == "attention_required" else None,
        "error_message": "ADEPT activation needs attention",
        "metadata": {"provider_loan_id": loan_id, "acsm_staging_path": "/tmp/existing.acsm",
                     **(metadata or {})},
    })


def test_existing_adept_attention_skips_provider_and_preserves_ledger(tmp_path, monkeypatch):
    first = _service(tmp_path)
    try:
        aid = _seed(first)
        original = first.storage.acquisition_get(aid)
        monkeypatch.setattr(first, "acquire_digital_loan", lambda *a, **kw: pytest.fail(
            "an unchanged attention must not retry downloading or fulfilling"
        ))
        result = first._acquire_active_loan_automatically(_loan())
        assert result["status"] == "attention_required"
        assert result["reason"] == "persisted_adept_attention"
        assert first.storage.acquisition_get(aid) == original
    finally:
        first.close()
    # A second app opening may have a different selected UI identity.
    reopened = _service(tmp_path)
    try:
        reopened.select_identity("alex")
        monkeypatch.setattr(reopened, "acquire_digital_loan", lambda *a, **kw: pytest.fail(
            "persistent attention must survive process restart"
        ))
        assert reopened._acquire_active_loan_automatically(_loan())["retry"] == "explicit_only"
        assert reopened.storage.acquisition_get(aid)["status"] == "attention_required"
    finally:
        reopened.close()


def test_guard_is_transaction_scoped_and_retryable_does_not_block():
    row = {"status": "attention_required", "strategy": "loan_auto",
           "executor": "kobo-adept", "entitlement_id": "entitlement-a",
           "metadata": {"provider_loan_id": "loan-a"}}
    call = lambda **opts: blocked_automatic_adept_retry(row, provider_loan_id="loan-a",
                  entitlement_id="entitlement-a", force_refresh=False, **opts)
    assert call()
    assert not blocked_automatic_adept_retry(row, provider_loan_id="loan-b",
              entitlement_id="entitlement-a", force_refresh=False)
    assert not blocked_automatic_adept_retry(row, provider_loan_id="loan-a",
              entitlement_id="entitlement-b", force_refresh=False)
    assert not blocked_automatic_adept_retry(row, provider_loan_id="loan-a",
              entitlement_id="entitlement-a", force_refresh=True)
    assert not blocked_automatic_adept_retry({**row, "status": "retryable_error"},
              provider_loan_id="loan-a", entitlement_id="entitlement-a", force_refresh=False)
    assert not blocked_automatic_adept_retry({**row, "executor": "provider"},
              provider_loan_id="loan-a", entitlement_id="entitlement-a", force_refresh=False)
    # Pre-fix records lacked provider_loan_id. Fail closed for unattended
    # replays rather than silently re-consuming the original ACSM.
    legacy = {**row, "metadata": {}}
    assert blocked_automatic_adept_retry(legacy, provider_loan_id="loan-a",
              entitlement_id="entitlement-a", force_refresh=False)


class _HostileProvenance:
    def __str__(self):
        raise AssertionError("never stringify private saved provenance")

    def __bool__(self):
        raise AssertionError("never coerce private saved provenance")


@pytest.mark.parametrize("metadata,prior_entitlement", [
    ([], "entitlement-a"),
    ("private account data", "entitlement-a"),
    ({"provider_loan_id": 12345}, "entitlement-a"),
    ({"provider_loan_id": _HostileProvenance()}, "entitlement-a"),
    ({"provider_loan_id": ""}, "entitlement-a"),
    ({"provider_loan_id": "loan-a"}, _HostileProvenance()),
    ({"provider_loan_id": "loan-a"}, False),
])
def test_corrupt_persisted_adept_attention_fails_closed_without_auto_retry(
    metadata, prior_entitlement,
):
    row = {
        "status": "attention_required",
        "strategy": "loan_auto",
        "executor": "kobo-adept",
        "metadata": metadata,
        "entitlement_id": prior_entitlement,
    }
    opts = {"provider_loan_id": "loan-a", "entitlement_id": "entitlement-a"}
    assert blocked_automatic_adept_retry(row, force_refresh=False, **opts)
    # A deliberate user refresh can still handle the failure via existing
    # acquisition guards; this change never performs the refresh itself.
    assert not blocked_automatic_adept_retry(row, force_refresh=True, **opts)



@pytest.mark.parametrize("corrupt_record", [
    [], "private saved row", True, 42, _HostileProvenance(),
])
def test_corrupt_saved_record_shape_never_retries_automatically(corrupt_record):
    """No truthiness or .get on an untrusted persisted record."""
    opts = {"provider_loan_id": "loan-a", "entitlement_id": "entitlement-a"}
    assert blocked_automatic_adept_retry(
        corrupt_record, force_refresh=False, **opts,
    )
    # The deliberate retry remains subject to the orchestration's usual
    # guards; this pure predicate itself performs no provider operation.
    assert not blocked_automatic_adept_retry(
        corrupt_record, force_refresh=True, **opts,
    )


def test_absent_or_empty_saved_record_does_not_invent_adept_attention():
    opts = {"provider_loan_id": "loan-a", "entitlement_id": "entitlement-a",
            "force_refresh": False}
    assert not blocked_automatic_adept_retry(None, **opts)
    assert not blocked_automatic_adept_retry({}, **opts)



@pytest.mark.parametrize("bad_override", [
    "true", "false", "1", 1, [True], {"force": True}, _HostileProvenance(),
])
def test_only_native_true_allows_explicit_adept_retry(bad_override):
    """Truthiness/coercion of a hostile caller value cannot reconsume an ACSM."""
    row = {
        "status": "attention_required", "strategy": "loan_auto",
        "executor": "kobo-adept", "entitlement_id": "entitlement-a",
        "metadata": {"provider_loan_id": "loan-a"},
    }
    opts = {"provider_loan_id": "loan-a", "entitlement_id": "entitlement-a"}
    assert blocked_automatic_adept_retry(row, force_refresh=bad_override, **opts)
    assert blocked_automatic_adept_retry(row, force_refresh=False, **opts)
    assert not blocked_automatic_adept_retry(row, force_refresh=True, **opts)


def test_exit_code_is_not_mistaken_for_adobe_license_error():
    assert adept_attention_code("adept_error", "acsmdownloader failed (1) exit 10",
                                executor="kobo-adept") == "adept_error"
    assert adept_attention_code("adept_error", "E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER (0x1003)",
                                executor="kobo-adept") == "adept_license_fulfilled_by_other_user"
    assert adept_attention_code("adept_error", "E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER",
                                executor="provider") == "adept_error"


def test_force_refresh_is_explicit_retry_of_same_loan(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    try:
        _seed(svc)
        counter = {"provider": 0}
        def download(*args, **kwargs):
            counter["provider"] += 1
            raise UserFacingError("service temporairement indisponible", code="transport_error")
        monkeypatch.setattr(svc, "acquire_digital_loan", download)
        automatic = svc._acquire_active_loan_automatically(_loan())
        assert automatic["status"] == "attention_required" and counter["provider"] == 0
        explicit = svc._acquire_active_loan_automatically(_loan(), force_refresh=True)
        assert explicit["status"] == "attention_required"
        assert counter["provider"] == 1
    finally:
        svc.close()


def test_acsm_adobe_refusal_is_persisted_then_blocked(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    acsm = tmp_path / "token.acsm"
    acsm.write_text(
        "<fulfillmentToken><format>application/epub+zip</format></fulfillmentToken>",
        encoding="utf-8",
    )
    try:
        svc.storage.adept_profile_binding_set("identity", "alex", "profile-id")
        monkeypatch.setattr(svc, "kobo_profiles", lambda: [{
            "profile_id": "profile-id", "name": "alex-ade", "fetch_capable": True,
        }])
        counter = {"provider": 0, "adept": 0}
        def download(_loan, **_kwargs):
            counter["provider"] += 1
            return Acquisition(
                provider_id="paris", credential_id="alex-paris", account_id="primary",
                item_id="book-a", loan_id="loan-a", kind="acsm",
                source_url="https://example.invalid/acsm", local_path=acsm,
                mime_type="application/vnd.adobe.adept+xml", drm="adept",
                expires_at=_loan.due_at, acsm_path=acsm, target_dir=tmp_path,
            )
        def adobe(_request):
            counter["adept"] += 1
            raise UserFacingError("E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER (0x1003)",
                                  code="adept_error")
        monkeypatch.setattr(svc, "acquire_digital_loan", download)
        monkeypatch.setattr(svc, "execute_acquisition_request", adobe)
        monkeypatch.setattr(svc, "runtime_diagnostic", lambda name: {"returncode": 10})
        first = svc._acquire_active_loan_automatically(_loan())
        assert first["status"] == "attention_required"
        assert first["code"] == "adept_license_fulfilled_by_other_user"
        assert counter == {"provider": 1, "adept": 1}
        rows = svc.storage.acquisitions(provider_id="paris")
        assert rows[0]["status"] == "attention_required"
        assert rows[0]["error_code"] == "adept_error"
        assert rows[0]["attention_code"] == "adept_license_fulfilled_by_other_user"
        assert rows[0]["metadata"]["provider_loan_id"] == "loan-a"
        assert rows[0]["metadata"]["acsm_staging_path"] == str(acsm)
        assert acsm.exists()
        second = svc._acquire_active_loan_automatically(_loan())
        assert second["reason"] == "persisted_adept_attention"
        assert counter == {"provider": 1, "adept": 1}
        assert acsm.exists()
    finally:
        svc.close()
