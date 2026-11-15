"""P1 #5225 — unknown provider bytes must never be indexed as a local loan.

The real inspector returns (None, False) when the content is neither a
PDF nor EPUB/ACSM. A generic extension must not bypass a negative verdict.
These are synthetic and do not claim canonical transaction recovery.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_provider_delivery import index_local_loan_artifact
from cifereca.providers import ProviderConfigurationError


def _loan():
    return NS(
        id="loan-local", record_id="edition-exact", title="Fiction",
        source_provider="provider-a", source_credential="credential-a",
        source_account_id="borrower-a", identity_id="alice",
    )


def _index(path, inspection, index):
    return index_local_loan_artifact(
        NS(catalog_index_path=index), _loan(), NS(identity_id="alice"),
        path, time=time, _TRACE_LOG=logging.getLogger("test.p1.5225"),
        inspect_artifact=lambda supplied: (
            inspection if supplied == path
            else pytest.fail("inspection did not use the exact artifact")
        ),
        ProviderConfigurationError=ProviderConfigurationError,
    )


@pytest.mark.parametrize("name", [
    "received.bin", "received.download", "received",
    "received.tmp", "received.unknown", "received.acsm",
])
@pytest.mark.parametrize("format_value", [
    None, "unknown", "bin", "acsm",
])
@pytest.mark.parametrize("validity", [False, None, 0])
def test_unrecognized_invalid_provider_bytes_cannot_enter_catalog_index(
    tmp_path, name, format_value, validity,
):
    path = tmp_path / name
    path.write_bytes(b"not an ebook, pdf or ACSM")
    calls = []
    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("invalid provider bytes cannot enter catalog index")
    with pytest.raises(ProviderConfigurationError, match="Artefact local invalide"):
        _index(path, NS(
            valid=validity, media_format=format_value, protected=None,
        ), forbidden)
    assert calls == []
    assert path.read_bytes() == b"not an ebook, pdf or ACSM"


@pytest.mark.parametrize("name,media_format", [
    ("received.bin", "epub"),
    ("received.download", "pdf"),
    ("received", "acsm"),
])
def test_positive_inspection_still_uses_exact_identity_scoped_index(
    tmp_path, name, media_format,
):
    path = tmp_path / name
    path.write_bytes(b"synthetic already verified input")
    calls = []
    def index(candidate, **kwargs):
        calls.append((candidate, kwargs))
        return {"status": "indexed"}
    inspection = NS(valid=True, media_format=media_format, protected=None)
    result_inspection, receipt = _index(path, inspection, index)
    assert result_inspection is inspection
    assert receipt == {"status": "indexed"}
    assert calls == [(path, {
        "title": "Fiction", "item_id": "edition-exact",
        "identity_id": "alice", "provider_id": "provider-a",
    })]


def test_real_unknown_content_inspection_returns_invalid_and_does_not_index(tmp_path):
    from cifereca.artifact_inspection import inspect_artifact

    path = tmp_path / "payload.download"
    path.write_bytes(b"unrecognized synthetic binary")
    inspection = inspect_artifact(path)
    assert inspection.valid is False and inspection.media_format is None

    def forbidden(*_a, **_kw):
        pytest.fail("real invalid inspection must stop before local index")

    with pytest.raises(ProviderConfigurationError, match="Artefact local invalide"):
        index_local_loan_artifact(
            NS(catalog_index_path=forbidden), _loan(), NS(identity_id="alice"),
            path, time=time, _TRACE_LOG=logging.getLogger("test.p1.5225.real"),
            inspect_artifact=inspect_artifact,
            ProviderConfigurationError=ProviderConfigurationError,
        )
