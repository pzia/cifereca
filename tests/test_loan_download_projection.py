"""Provider-stage provenance and staging are stable across projection extraction."""
from types import SimpleNamespace as NS
from pathlib import Path
from unittest.mock import Mock

import pytest

from cifereca.core import CiferecaCore
from cifereca.models import Account, Loan
from cifereca import services


@pytest.mark.parametrize("borrower", ["", "linked-reader"])
@pytest.mark.parametrize("filename", ["book.ACSM", "book.pdf"])
def test_provider_stage_retains_borrower_and_separates_acsm_from_library(
    tmp_path, monkeypatch, borrower, filename,
):
    core = CiferecaCore.open(config_path=tmp_path / "config.json", database_path=tmp_path / "db")
    try:
        service = core.library
        core.config.set("library.directory", str(tmp_path / "library"))
        service.storage.provider_connection_put({
            "identity_id": "default", "provider_id": "paris", "credential_id": "test",
        })
        service.accounts = [Account(
            display_name="Reader", unique_id=borrower, provider_id="paris",
            credential_id="test", identity_id="default", is_primary=not borrower,
        )]
        loan = Loan(
            id="loan-1", provider="PNB", medium="digital", record_id=None,
            title="Été", document_type="Roman", thumbnail_url="https://example.invalid/cover",
            source_provider="paris", source_credential="test", source_account_id=borrower,
            ebook_url="https://example.invalid/source", due_at="2099-01-01T00:00:00Z",
        )
        events = []
        staged_paths = []
        def download(received, account, target):
            assert received is loan
            assert account.unique_id == borrower
            path = target / filename
            path.write_bytes(b"provider output")
            staged_paths.append(path)
            events.append("download")
            return path, "https://example.invalid/download"
        session = NS(provider_id="paris", download_digital=download)
        monkeypatch.setattr(service, "_session_for_source", lambda *_: session)
        def hook(item, path, url, target):
            assert path == target / filename
            assert path.read_bytes() == b"provider output"
            assert item.record_id == loan.id
            assert item.source_credential == "test"
            assert item.friendly_url == loan.ebook_url
            assert item.download_url == url
            assert item.format == "ACSM"  # historical hook contract, including final files
            events.append("hook")
        monkeypatch.setattr(service.acquisition, "run_post_download", hook)
        def mime(path, item):
            assert path.exists()  # publication already happened for direct artifacts
            events.append("mime")
            return "application/test"
        monkeypatch.setattr(service.acquisition, "mime_type_for", mime)
        search_factory = Mock(wraps=services.SearchResult)
        acquisition_factory = Mock(wraps=services.Acquisition)
        monkeypatch.setattr(services, "SearchResult", search_factory)
        monkeypatch.setattr(services, "Acquisition", acquisition_factory)
        result = service.acquire_digital_loan(loan)
        assert events == ["download", "hook", "mime"]
        search_factory.assert_called_once()
        acquisition_factory.assert_called_once()
        assert result.account_id == (borrower or "primary")
        assert result.provider_id == "paris"
        assert result.credential_id == "test"
        assert result.item_id == result.loan_id == loan.id
        assert result.expires_at == loan.due_at
        assert result.mime_type == "application/test"
        assert result.metadata["format"] == Path(filename).suffix[1:].upper()
        assert result.metadata["cover_url"] == loan.thumbnail_url
        assert result.path.read_bytes() == b"provider output"
        if filename.endswith("ACSM"):
            assert result.kind == "acsm" and result.drm == "adept"
            assert result.path == result.acsm_path == staged_paths[0]
            assert result.artifact_path is None
            assert result.path.parent != result.target_dir
        else:
            assert result.kind == "already_local" and result.drm == "unknown"
            assert result.path == result.artifact_path == result.target_dir / filename
            assert result.acsm_path is None
            assert not staged_paths[0].exists()
    finally:
        core.close()
