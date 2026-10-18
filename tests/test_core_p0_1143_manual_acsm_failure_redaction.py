"""P0 #1143: manual ACSM import never persists raw failure details.

The historical real SQLite import fixture is used with synthetic PDF output.
No Adobe request, real ACSM, provider account, upload or GTK is involved.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

import cifereca.services as facade
from cifereca.identities import AdeptProfileResolutionError
from cifereca.providers import ProviderConfigurationError
from tests.test_acsm_import_preservation import importer


PRIVATE = "https://private.invalid/loan.acsm?token=SECRET_DRM_ERROR"


def _acsm(tmp_path):
    source = tmp_path / "private.acsm"
    source.write_text(
        "<fulfillment><id>synthetic-secret-boundary</id>"
        "<format>application/pdf</format></fulfillment>",
        encoding="utf-8",
    )
    return source


def _assert_ledger_safe(service, *, state, code, message):
    [row] = service.storage.acquisitions()
    assert row["status"] == state
    assert row["error_code"] == code
    assert row["error_message"] == message
    assert "SECRET_DRM_ERROR" not in str({
        "code": row["error_code"], "message": row["error_message"],
    })
    return row


def test_runtime_failure_keeps_original_and_retryable_ledger_without_secrets(
    tmp_path, importer, monkeypatch,
):
    service, requests = importer
    source = _acsm(tmp_path)
    original = source.read_bytes()

    class SecretRuntimeError(RuntimeError):
        def __str__(self):
            raise AssertionError("Runtime failure may never be stringified")

    monkeypatch.setattr(
        service, "execute_acquisition_request",
        lambda *_args: (_ for _ in ()).throw(SecretRuntimeError(PRIVATE)),
    )
    with pytest.raises(SecretRuntimeError):
        service.import_acsm(source)
    row = _assert_ledger_safe(
        service, state="retryable_error",
        code="manual_acsm_failed", message="Import ACSM échoué",
    )
    assert source.read_bytes() == original
    assert row["metadata"]["source_acsm_sha256"]
    assert row["metadata"]["staging"]
    assert not requests


@pytest.mark.parametrize(("incoming_code", "expected"), [
    ("adept_profile_ambiguous", "adept_profile_ambiguous"),
    ("adept_profile_required", "adept_profile_required"),
    (PRIVATE, "adept_profile_required"),
])
def test_profile_refusal_retains_safe_remediation_code_without_raw_message(
    tmp_path, importer, monkeypatch, incoming_code, expected,
):
    service, requests = importer
    source = _acsm(tmp_path)

    def unavailable(_identity):
        raise AdeptProfileResolutionError(PRIVATE, code=incoming_code)

    monkeypatch.setattr(service, "resolve_adept_profile_for_identity", unavailable)
    result = service.import_acsm(source)
    assert result["status"] == "attention_required"
    assert result["code"] == expected
    row = _assert_ledger_safe(
        service, state="attention_required",
        code=expected, message="Profil ADEPT indisponible",
    )
    assert row["attention_code"] == expected
    assert PRIVATE not in repr({"status": result["status"], "code": result["code"]})
    assert not requests


def test_invalid_artifact_reason_never_enters_error_or_ledger(
    tmp_path, importer, monkeypatch,
):
    service, requests = importer
    source = _acsm(tmp_path)
    monkeypatch.setattr(
        facade, "inspect_artifact",
        lambda *_args: NS(valid=False, media_format="pdf", reason=PRIVATE),
    )
    with pytest.raises(ProviderConfigurationError, match="Artifact ACSM invalide") as error:
        service.import_acsm(source)
    assert PRIVATE not in str(error.value)
    _assert_ledger_safe(
        service, state="retryable_error",
        code="manual_acsm_failed", message="Import ACSM échoué",
    )
    assert len(requests) == 1


def test_index_failure_reason_never_enters_error_or_ledger(
    tmp_path, importer, monkeypatch,
):
    service, requests = importer
    source = _acsm(tmp_path)
    monkeypatch.setattr(
        service, "catalog_index_path",
        lambda *_args, **_kwargs: {"status": "failed", "reason": PRIVATE},
    )
    with pytest.raises(ProviderConfigurationError, match="Indexation ACSM impossible") as error:
        service.import_acsm(source)
    assert PRIVATE not in str(error.value)
    _assert_ledger_safe(
        service, state="retryable_error",
        code="manual_acsm_failed", message="Import ACSM échoué",
    )
    assert len(requests) == 1
