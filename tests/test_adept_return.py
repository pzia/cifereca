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


def test_confirmed_return_refreshes_public_inventory_before_reporting_success(tmp_path: Path):
    service = _service(tmp_path)
    acquisition_id = _acquisition(service, tmp_path)
    reads = iter([
        {"loans": [_loan(returned=False, token_present=True)]},
        {"loans": [_loan(returned=True, token_present=False)]},
    ])
    calls = []
    runtime = SimpleNamespace(loans=lambda profile: (calls.append(profile) or next(reads)))
    service._kobo_adapter = lambda: runtime
    service.execute_drm_mutation = lambda request: {
        "ok": True,
        "metadata": {"runtime_result": {
            "ok": True,
            "operation": "loan_return",
            "loan_id": "registry-id",
            "profile": "hb",
        }},
    }
    try:
        result = service.return_adept_acquisition(acquisition_id)

        assert result["ok"] is True
        assert result["verification"] == "confirmed"
        assert calls == ["hb", "hb"]
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["loan_state"] == "returned"
        assert binding["token_present"] is False
        assert binding["return_verification"] == "confirmed"
        assert binding["return_available"] is False
    finally:
        service.close()


def test_refresh_failure_after_return_never_reports_success(tmp_path: Path):
    service = _service(tmp_path)
    acquisition_id = _acquisition(service, tmp_path)
    calls = []

    def loans(profile):
        calls.append(profile)
        if len(calls) == 1:
            return {"loans": [_loan(returned=False, token_present=True)]}
        raise RuntimeError("inventory unavailable")

    service._kobo_adapter = lambda: SimpleNamespace(loans=loans)
    service.execute_drm_mutation = lambda request: {
        "ok": True,
        "metadata": {"runtime_result": {
            "ok": True,
            "operation": "loan_return",
            "loan_id": "registry-id",
            "profile": "hb",
        }},
    }
    try:
        with pytest.raises(AttentionRequired) as exc:
            service.return_adept_acquisition(acquisition_id)

        assert exc.value.code == "adept_return_verification_required"
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["return_verification"] == "verification_failed"
        assert binding["return_available"] is False
    finally:
        service.close()


def test_unverified_return_state_is_explicit(tmp_path: Path):
    service = _service(tmp_path)
    acquisition_id = _acquisition(service, tmp_path)
    service._kobo_adapter = lambda: SimpleNamespace(
        loans=lambda profile: {"loans": [_loan(returned=True, token_present=True)]}
    )
    try:
        with pytest.raises(AttentionRequired) as exc:
            service.return_adept_acquisition(acquisition_id)

        assert exc.value.code == "adept_return_token_conflict"
        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["loan_state"] == "returned"
        assert binding["return_verification"] == "verification_failed"
        assert binding["return_available"] is False
    finally:
        service.close()


def test_unknown_acquisition_is_refused(tmp_path: Path):
    service = _service(tmp_path)
    try:
        with pytest.raises(AttentionRequired) as exc:
            service.return_adept_acquisition("missing")

        assert exc.value.code == "adept_acquisition_unknown"
    finally:
        service.close()
