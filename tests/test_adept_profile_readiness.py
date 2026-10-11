from __future__ import annotations

from pathlib import Path

from cifereca.acquisition import Acquisition
from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import Loan, Account
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _service(tmp_path: Path) -> LibraryService:
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set(
        "identities",
        [
            {
                "id": "alex",
                "label": "Alex",
                "directory_name": "alex",
                "preferences": {"reading_languages": ["fr"]},
            }
        ],
    )
    config.set("providers.accounts", [])
    return LibraryService(config, Storage(tmp_path / "db.sqlite3"), Diagnostics())


def test_bound_profile_explicitly_not_fetch_ready_blocks_before_adept(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    try:
        service.storage.provider_connection_put({
            "identity_id": "alex",
            "provider_id": "paris",
            "credential_id": "alex-paris",
            "label": "Paris Alex",
        })
        service.accounts.append(Account(unique_id="primary", provider_id="paris", credential_id="alex-paris", identity_id="alex"))
        service.storage.adept_profile_binding_set(
            "identity",
            "alex",
            "profile-stable-alex",
        )
        service.accounts = [
            Account(display_name="Primary Alex", unique_id="", is_primary=True,
                    provider_id="paris", credential_id="alex-paris", identity_id="alex"),
        ]
        monkeypatch.setattr(
            service,
            "kobo_profiles",
            lambda: [{
                "profile_id": "profile-stable-alex",
                "name": "alex-ade",
                "fetch_capable": False,
                "provisioning": {
                    "reader_backed_identity_ready": True,
                    "acsm_fetch_ready": False,
                },
            }],
        )

        loan = Loan(
            id="loan-alex-not-ready",
            provider="PNB",
            medium="digital",
            record_id="book-alex-not-ready",
            title="Alex Not Ready",
            source_provider="paris",
            source_credential="alex-paris",
            source_account_id="primary",
            identity_id="alex",
            ebook_url="https://example.invalid/acsm",
            due_at="2099-01-01T00:00:00Z",
        )
        service._persist_active_loans("alex", [loan])

        acsm = tmp_path / "alex-not-ready.acsm"
        acsm.write_text(
            '<fulfillmentToken><format>application/epub+zip</format></fulfillmentToken>',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            service,
            "acquire_digital_loan",
            lambda _loan: Acquisition(
                provider_id="paris",
                credential_id="alex-paris",
                account_id="primary",
                item_id="book-alex-not-ready",
                loan_id="loan-alex-not-ready",
                kind="acsm",
                source_url="https://example.invalid/acsm",
                local_path=acsm,
                mime_type="application/vnd.adobe.adept+xml",
                drm="adept",
                expires_at=loan.due_at,
                acsm_path=acsm,
                target_dir=tmp_path,
            ),
        )

        called = {"adept": False}

        def must_not_call(_request):
            called["adept"] = True
            raise AssertionError("ADEPT runtime must not run for a non fetch-ready profile")

        monkeypatch.setattr(service, "execute_acquisition_request", must_not_call)

        result = service._acquire_active_loan_automatically(loan)

        assert result == {
            "status": "attention_required",
            "loan_id": "loan-alex-not-ready",
            "code": "adept_profile_acsm_not_ready",
            "error": "Le profil Kobo-Adept lié à cette identité n'est pas prêt pour les acquisitions ACSM",
        }
        assert called["adept"] is False
        assert acsm.exists()

        row = next(
            row
            for row in service.storage.acquisitions()
            if row["item_id"] == "book-alex-not-ready"
        )
        assert row["status"] == "attention_required"
        assert row["executor"] == "kobo-adept"
        assert row["attention_code"] == "adept_profile_acsm_not_ready"
    finally:
        service.close()
