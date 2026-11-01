from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.plugin_api import AttentionRequired
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _service(tmp_path: Path) -> LibraryService:
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set("providers.accounts", [])
    service = LibraryService(config, Storage(tmp_path / "db.sqlite3"), Diagnostics(level="detailed"))
    service._adept_return_capability = lambda: True
    service.kobo_profiles = lambda: [{"profile_id": "stable-profile", "name": "hb"}]
    return service


def _acquisition(service: LibraryService, tmp_path: Path) -> str:
    path = tmp_path / "EBooks" / "hugues" / "paris" / "Livre.epub"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"existing-local-artifact")
    return service.storage.acquisition_put({
        "acquisition_id": "acq-1",
        "identity_id": "default",
        "provider_id": "paris",
        "credential_id": "reader",
        "account_id": "account",
        "item_id": "book",
        "title": "Livre",
        "status": "complete",
        "executor": "kobo-adept",
        "metadata": {
            "path": str(path),
            "adept_binding": {
                "adept_profile_id": "stable-profile",
                "adept_profile_name": "hb",
                "adept_loan_id": "registry-id",
                "loan_state": "active",
                "token_present": True,
                "return_available": True,
            },
        },
    })


def _loan(*, returned: bool, token_present: bool, expired: bool = False):
    return {
        "id": "registry-id",
        "returned": returned,
        "expired": expired,
        "token_present": token_present,
    }


@pytest.mark.parametrize("inventory", [
    {"loans": [_loan(returned=False, token_present=True)]},
    {"loans": [_loan(returned=True, token_present=False)]},
    {"loans": [_loan(returned=True, token_present=True)]},
])
def test_retired_adept_return_never_dispatches_or_modifies_binding(tmp_path: Path, inventory):
    service = _service(tmp_path)
    acquisition_id = _acquisition(service, tmp_path)
    before = service.storage.acquisition_get(acquisition_id)
    artifact = Path(before["metadata"]["path"])
    content = artifact.read_bytes()
    accessed = []
    service._kobo_adapter = lambda: accessed.append("adapter") or SimpleNamespace(
        loans=lambda profile: inventory,
    )
    service.execute_drm_mutation = lambda _: pytest.fail("ADEPT mutation forbidden")
    try:
        with pytest.raises(AttentionRequired) as exc:
            service.return_adept_acquisition(acquisition_id)
        assert exc.value.code == "loan_return_removed"
        assert accessed == []
        assert service.storage.acquisition_get(acquisition_id) == before
        assert artifact.read_bytes() == content
    finally:
        service.close()


def test_missing_or_foreign_acquisition_never_reaches_storage_or_adept(tmp_path: Path):
    service = _service(tmp_path)
    try:
        service._kobo_adapter = lambda: pytest.fail("ADEPT adapter forbidden")
        with pytest.raises(AttentionRequired) as exc:
            service.return_adept_acquisition("missing")
        assert exc.value.code == "loan_return_removed"
        assert service.storage.acquisitions(provider_id="paris") == []
    finally:
        service.close()


def test_repeat_return_cannot_resume_historical_adept_checkpoint(tmp_path: Path):
    service = _service(tmp_path)
    aid = _acquisition(service, tmp_path)
    try:
        before = service.storage.acquisition_get(aid)
        for _ in range(2):
            with pytest.raises(AttentionRequired) as exc:
                service.return_adept_acquisition(aid)
            assert exc.value.code == "loan_return_removed"
        assert service.storage.acquisition_get(aid) == before
        assert Path(before["metadata"]["path"]).read_bytes() == b"existing-local-artifact"
    finally:
        service.close()
