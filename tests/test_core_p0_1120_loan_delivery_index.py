"""#1120: provider/artefact stages move without changing automatic-loan flow."""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import cifereca.services as facade
from cifereca.core import loan_provider_delivery
from cifereca.providers import ProviderConfigurationError
from tests.test_core_0977_adept_retry_guard import _loan, _service


@pytest.mark.parametrize("force", [False, True])
def test_extracted_provider_stage_keeps_explicit_force_refresh_call_semantics(
    tmp_path, monkeypatch, force,
):
    service = _service(tmp_path)
    calls = []
    path = tmp_path / "fictional-provider.bin"
    path.write_bytes(b"fictional")
    acquisition = NS(kind="direct", path=path)
    def fake_provider(loan, **kwargs):
        calls.append((loan.id, kwargs))
        return acquisition
    monkeypatch.setattr(service, "acquire_digital_loan", fake_provider)
    try:
        result = loan_provider_delivery.acquire_provider_loan(
            service, _loan(), force_refresh=force,
            time=facade.time, _TRACE_LOG=facade._TRACE_LOG,
        )
        assert result is acquisition
        assert calls == [("loan-a", {"force_refresh": True} if force else {})]
    finally:
        service.close()


def test_extracted_index_stage_rejects_missing_artifact_before_catalog(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        monkeypatch.setattr(
            service, "catalog_index_path",
            lambda *_a, **_kw: pytest.fail("missing bytes must not be indexed"),
        )
        with pytest.raises(ProviderConfigurationError, match="aucun artefact local"):
            loan_provider_delivery.index_local_loan_artifact(
                service, _loan(), NS(identity_id="alex"),
                tmp_path / "missing.epub",
                time=facade.time, _TRACE_LOG=facade._TRACE_LOG,
                inspect_artifact=facade.inspect_artifact,
                ProviderConfigurationError=facade.ProviderConfigurationError,
            )
    finally:
        service.close()


def test_extracted_index_stage_preserves_provider_and_identity_coordinates(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    file = tmp_path / "real-local.pdf"
    file.write_bytes(b"%PDF-1.4\n% synthetic\n%%EOF\n")
    inspect = NS(valid=True, media_format="pdf", protected=False)
    calls = []
    monkeypatch.setattr(
        service, "catalog_index_path",
        lambda path, **kw: (
            calls.append((path, kw))
            or {"status": "indexed", "artifact_id": "sha256:fictional"}
        ),
    )
    try:
        inspection, indexed = loan_provider_delivery.index_local_loan_artifact(
            service, _loan(), NS(identity_id="alex"), file,
            time=facade.time, _TRACE_LOG=facade._TRACE_LOG,
            inspect_artifact=lambda path: inspect,
            ProviderConfigurationError=facade.ProviderConfigurationError,
        )
        assert inspection is inspect
        assert indexed["status"] == "indexed"
        assert calls == [(file, {
            "title": "Un livre", "item_id": "book-a",
            "identity_id": "alex", "provider_id": "paris",
        })]
    finally:
        service.close()


def test_extracted_index_stage_preserves_catalog_refusal_and_file(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    file = tmp_path / "not-indexed.epub"
    file.write_bytes(b"synthetic book")
    original = file.read_bytes()
    monkeypatch.setattr(
        service, "catalog_index_path",
        lambda *_a, **_kw: {"status": "invalid", "reason": "fictional"},
    )
    try:
        with pytest.raises(ProviderConfigurationError, match="Indexation locale impossible"):
            loan_provider_delivery.index_local_loan_artifact(
                service, _loan(), NS(identity_id="alex"), file,
                time=facade.time, _TRACE_LOG=facade._TRACE_LOG,
                inspect_artifact=lambda path: NS(
                    valid=True, media_format="epub", protected=False,
                ),
                ProviderConfigurationError=facade.ProviderConfigurationError,
            )
        assert file.read_bytes() == original
    finally:
        service.close()


def test_automatic_loan_facade_still_records_original_provider_and_catalog_contract(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    local = tmp_path / "acquired.pdf"
    local.write_bytes(b"%PDF-1.4\n% synthetic output\n%%EOF\n")
    calls = []
    monkeypatch.setattr(
        service, "acquire_digital_loan",
        lambda loan, **opts: (
            calls.append(("provider", loan.id, opts))
            or NS(kind="direct", path=local, acsm_path=None)
        ),
    )
    monkeypatch.setattr(
        facade, "inspect_artifact",
        lambda path: NS(valid=True, media_format="pdf", protected=False),
    )
    monkeypatch.setattr(
        service, "catalog_index_path",
        lambda path, **kw: (
            calls.append(("index", kw["identity_id"], kw["item_id"]))
            or {"status": "indexed", "artifact_id": "sha256:fake"}
        ),
    )
    try:
        result = service._acquire_active_loan_automatically(_loan())
        assert result["status"] == "complete"
        assert result["loan_id"] == "loan-a"
        assert result["decision"] == "acquire"
        assert calls == [
            ("provider", "loan-a", {}),
            ("index", "alex", "book-a"),
        ]
        saved = service.storage.acquisitions(provider_id="paris")
        assert len(saved) == 1
        assert saved[0]["status"] == "complete"
        assert saved[0]["metadata"]["acquisition_decision"]["action"] == "acquire"
    finally:
        service.close()


PRIVATE = "https://private.invalid/LOAN/file.epub?auth=PRIVATE_INDEX_TOKEN"


@pytest.mark.parametrize("stage", ["inspect", "catalog"])
def test_untrusted_artifact_failure_reason_is_not_exposed_to_caller_or_logs(
    tmp_path, monkeypatch, caplog, stage,
):
    service = _service(tmp_path)
    file = tmp_path / "local.epub"
    file.write_bytes(b"offline fixture")
    catalog = []
    def index(path, **kwargs):
        catalog.append((path, kwargs))
        return {"status": "invalid", "reason": PRIVATE}
    monkeypatch.setattr(service, "catalog_index_path", index)
    inspector = lambda _path: NS(
        valid=stage == "catalog", media_format="epub", protected=False,
        reason=PRIVATE,
    )
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            with pytest.raises(ProviderConfigurationError) as failure:
                loan_provider_delivery.index_local_loan_artifact(
                    service, _loan(), NS(identity_id="alex"), file,
                    time=facade.time, _TRACE_LOG=facade._TRACE_LOG,
                    inspect_artifact=inspector,
                    ProviderConfigurationError=facade.ProviderConfigurationError,
                )
        assert str(failure.value) == (
            "Artefact local invalide" if stage == "inspect"
            else "Indexation locale impossible"
        )
        assert len(catalog) == (1 if stage == "catalog" else 0)
        assert PRIVATE not in str(failure.value) + caplog.text
        assert file.read_bytes() == b"offline fixture"
    finally:
        service.close()
