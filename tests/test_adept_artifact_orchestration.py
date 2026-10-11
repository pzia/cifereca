from __future__ import annotations

from pathlib import Path
import zipfile

from cifereca.acquisition import Acquisition
from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import Loan, Account
from cifereca.services import LibraryService
from cifereca.storage import Storage


PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def _write_epub(path: Path, payload: bytes = b"readable") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("content.xhtml", payload)


def _service(tmp_path: Path) -> LibraryService:
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set(
        "identities",
        [{
            "id": "default",
            "label": "Hugues",
            "directory_name": "hugues",
            "preferences": {"reading_languages": ["fr"]},
        }],
    )
    config.set("providers.accounts", [])
    service = LibraryService(config, Storage(tmp_path / "db.sqlite3"), Diagnostics())
    service.storage.provider_connection_put({
        "identity_id": "default",
        "provider_id": "paris",
        "credential_id": "p",
        "label": "Paris",
    })
    service.accounts.append(Account(unique_id="primary", provider_id="paris", credential_id="p", identity_id="default"))
    service.storage.adept_profile_binding_set("identity", "default", "profile-hb")
    service.accounts = [
        Account(display_name="Primary", unique_id="", is_primary=True,
                provider_id="paris", credential_id="p", identity_id="default"),
    ]
    service.kobo_profiles = lambda: [{
        "profile_id": "profile-hb",
        "name": "hb",
        "fetch_capable": True,
        "provisioning": {"acsm_fetch_ready": True},
    }]
    service.runtime_diagnostic = lambda _plugin_id: {}
    return service


def _loan(*, loan_id: str, record_id: str, title: str) -> Loan:
    return Loan(
        id=loan_id,
        provider="PNB",
        medium="digital",
        record_id=record_id,
        title=title,
        source_provider="paris",
        source_credential="p",
        source_account_id="primary",
        identity_id="default",
        ebook_url="https://example.invalid/acsm",
        due_at="2099-01-01T00:00:00Z",
    )


def test_loan_auto_detects_pdf_from_acsm_and_cleans_source_after_success(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    try:
        loan = _loan(loan_id="loan-pdf", record_id="book-pdf", title="PDF loan")
        service._persist_active_loans("default", [loan])

        acsm = tmp_path / "loan-pdf.acsm"
        acsm.write_text(
            '<fulfillmentToken xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<metadata><dc:format>application/pdf</dc:format></metadata>"
            "</fulfillmentToken>",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            service,
            "acquire_digital_loan",
            lambda _loan: Acquisition(
                provider_id="paris",
                credential_id="p",
                account_id="primary",
                item_id="book-pdf",
                loan_id="loan-pdf",
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

        captured: dict = {}

        def execute(request):
            captured.update(request)
            destination = Path(request["destination"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(PDF)
            return {
                "ok": True,
                "profile": "hb",
                "loan_id": "adept-loan-pdf",
                "artifact": {"path": str(destination), "format": "pdf", "drm": "none"},
                "hook": {"ok": True, "artifact_changed": True},
            }

        monkeypatch.setattr(service, "execute_acquisition_request", execute)

        result = service._acquire_active_loan_automatically(loan)

        assert result["status"] == "complete"
        assert captured["format"] == "pdf"
        assert captured["destination"].endswith(".pdf")
        assert "run_hook" not in captured["options"]
        final_path = Path(result["path"])
        assert final_path.suffix == ".pdf"
        assert final_path.read_bytes() == PDF
        assert not acsm.exists()

        row = next(
            row
            for row in service.storage.acquisitions(status="complete")
            if row["item_id"] == "book-pdf"
        )
        assert row["metadata"]["artifact_format"] == "pdf"
        assert row["metadata"]["post_processed"] is True
        assert row["metadata"]["hook_reported"] is True
    finally:
        service.close()


def test_loan_auto_repairs_legacy_adept_artifact_via_repairing_file(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    try:
        loan = _loan(loan_id="loan-repair", record_id="book-repair", title="Repair")
        service._persist_active_loans("default", [loan])

        destination = (
            service.library_identity_provider_dir("default", "paris")
            / service._loan_destination_name(loan.title, ".epub")
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"protected-old")

        entitlement = next(
            row
            for row in service.storage.entitlements(provider_id="paris", state="active")
            if row.get("loan_id") == loan.id
        )
        service.storage.acquisition_put({
            "entitlement_id": entitlement["entitlement_id"],
            "provider_id": "paris",
            "credential_id": "p",
            "account_id": "primary",
            "identity_id": "default",
            "item_id": "book-repair",
            "title": "Repair",
            "status": "complete",
            "strategy": "loan_auto",
            "executor": "kobo-adept",
            "metadata": {"path": str(destination)},
        })

        acsm = tmp_path / "repair.acsm"
        acsm.write_text(
            "<fulfillmentToken><format>application/epub+zip</format></fulfillmentToken>",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            service,
            "acquire_digital_loan",
            lambda _loan: Acquisition(
                provider_id="paris",
                credential_id="p",
                account_id="primary",
                item_id="book-repair",
                loan_id="loan-repair",
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

        captured: dict = {}

        def execute(request):
            captured.update(request)
            repairing = Path(request["destination"])
            assert repairing != destination
            assert repairing.name.endswith(".repairing.epub")
            assert destination.read_bytes() == b"protected-old"
            _write_epub(repairing, b"readable-new")
            return {
                "ok": True,
                "profile": "hb",
                "loan_id": "adept-loan-repair",
                "artifact": {"path": str(repairing), "format": "epub", "drm": "none"},
            }

        monkeypatch.setattr(service, "execute_acquisition_request", execute)

        result = service._acquire_active_loan_automatically(loan)

        assert result["status"] == "complete"
        assert Path(result["path"]) == destination
        assert zipfile.is_zipfile(destination)
        assert b"readable-new" in zipfile.ZipFile(destination).read("content.xhtml")
        assert not Path(captured["destination"]).exists()
        assert not acsm.exists()

        row = next(
            row
            for row in service.storage.acquisitions(status="complete")
            if row["item_id"] == "book-repair"
        )
        assert row["metadata"]["path"] == str(destination)
        assert row["metadata"]["post_processed"] is True
    finally:
        service.close()
