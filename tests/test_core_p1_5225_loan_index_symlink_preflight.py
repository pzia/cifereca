"""#5225 — legacy auto-loan index preflight must not inspect path aliases.

This is a targeted defense-in-depth check, NOT proof of crash-safe
FS/SQLite convergence. No provider request, ADEPT call or real loan.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_provider_delivery import (
    acquire_provider_loan, index_local_loan_artifact,
)
from cifereca.providers import ProviderConfigurationError


def fake_loan():
    return NS(
        id="loan-synthetic", record_id="edition-synthetic",
        title="Fiction", source_provider="synthetic",
        source_credential="credential-synthetic",
        source_account_id="borrower-synthetic",
        identity_id="alice",
    )


@pytest.mark.parametrize("alias", ["file", "parent"])
def test_symlinked_loan_output_is_rejected_before_any_content_inspection(
    tmp_path, alias,
):
    external = tmp_path / "external"
    external.mkdir()
    real = external / "document.pdf"
    real.write_bytes(b"%PDF-1.7\nprivate synthetic fixture\n%%EOF")
    if alias == "file":
        path = tmp_path / "linked.pdf"
        path.symlink_to(real)
    else:
        directory = tmp_path / "linked-directory"
        directory.symlink_to(external, target_is_directory=True)
        path = directory / "document.pdf"

    def forbidden(*_args, **_kwargs):
        pytest.fail("A linked document must not be inspected or indexed")

    service = NS(catalog_index_path=forbidden)
    with pytest.raises(ProviderConfigurationError, match="Artefact local invalide"):
        index_local_loan_artifact(
            service, fake_loan(), NS(identity_id="alice"), path,
            time=time, _TRACE_LOG=logging.getLogger("cifereca.test"),
            inspect_artifact=forbidden,
            ProviderConfigurationError=ProviderConfigurationError,
        )
    assert real.read_bytes() == b"%PDF-1.7\nprivate synthetic fixture\n%%EOF"
    assert path.is_symlink() is (alias == "file")


def test_normal_physical_pdf_keeps_existing_index_and_exact_owner(tmp_path):
    path = tmp_path / "physical.pdf"
    path.write_bytes(b"%PDF-1.7\nfictional fixture\n%%EOF")
    calls = []
    inspection = NS(valid=True, media_format="pdf", protected=False)
    def catalog_index(received, **scope):
        calls.append((received, scope))
        return {"status": "indexed", "artifact_id": "sha256:synthetic"}
    service = NS(catalog_index_path=catalog_index)
    inspected, indexed = index_local_loan_artifact(
        service, fake_loan(), NS(identity_id="alice"), path,
        time=time, _TRACE_LOG=logging.getLogger("cifereca.test"),
        inspect_artifact=lambda received: (
            inspection if received == path else pytest.fail("unexpected path")
        ),
        ProviderConfigurationError=ProviderConfigurationError,
    )
    assert inspected is inspection and indexed["status"] == "indexed"
    assert calls == [(path, {
        "title": "Fiction", "item_id": "edition-synthetic",
        "identity_id": "alice", "provider_id": "synthetic",
    })]


@pytest.mark.parametrize("invalid", [None, "", "/tmp/not-a-path", True, object()])
def test_nonpath_receipts_do_not_reach_inspection(invalid):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Invalid output must not be inspected")
    with pytest.raises(ProviderConfigurationError):
        index_local_loan_artifact(
            NS(catalog_index_path=forbidden),
            fake_loan(), NS(identity_id="alice"), invalid,
            time=time, _TRACE_LOG=logging.getLogger("cifereca.test"),
            inspect_artifact=forbidden,
            ProviderConfigurationError=ProviderConfigurationError,
        )


def test_symlinked_grandparent_is_rejected_before_inspection_or_index(tmp_path):
    external = tmp_path / "private-target"
    nested = external / "nested"
    nested.mkdir(parents=True)
    private = nested / "book.epub"
    private.write_bytes(b"synthetic bytes must not be read")
    link = tmp_path / "outer-aliased"
    link.symlink_to(external, target_is_directory=True)
    candidate = link / "nested" / "book.epub"

    assert not candidate.is_symlink()
    assert not candidate.parent.is_symlink()
    assert candidate.parent.parent.is_symlink()

    def forbidden(*_args, **_kwargs):
        pytest.fail("No inspection or index through a symlink ancestor")

    with pytest.raises(ProviderConfigurationError, match="Artefact local invalide"):
        index_local_loan_artifact(
            NS(catalog_index_path=forbidden),
            fake_loan(), NS(identity_id="alice"), candidate,
            time=time, _TRACE_LOG=logging.getLogger("cifereca.test"),
            inspect_artifact=forbidden,
            ProviderConfigurationError=ProviderConfigurationError,
        )
    assert private.read_bytes() == b"synthetic bytes must not be read"


@pytest.mark.parametrize("alias", ["file", "parent", "grandparent"])
def test_provider_trace_never_stats_symlinked_artifact(alias, tmp_path):
    external = tmp_path / "private-target"
    nested = external / "nested"
    nested.mkdir(parents=True)
    private = nested / "book.pdf"
    private.write_bytes(b"SECRET synthetic fixture size")

    if alias == "file":
        candidate = tmp_path / "alias.pdf"
        candidate.symlink_to(private)
    elif alias == "parent":
        candidate_dir = tmp_path / "alias-dir"
        candidate_dir.symlink_to(nested, target_is_directory=True)
        candidate = candidate_dir / "book.pdf"
    else:
        outer = tmp_path / "alias-outer"
        outer.symlink_to(external, target_is_directory=True)
        candidate = outer / "nested" / "book.pdf"

    logs = []
    trace = NS(info=lambda template, *args: logs.append((template, args)))
    acquisition = NS(kind="direct", path=candidate)
    service = NS(acquire_digital_loan=lambda _loan: acquisition)
    assert acquire_provider_loan(
        service, fake_loan(), force_refresh=False,
        time=time, _TRACE_LOG=trace,
    ) is acquisition
    end = [args for template, args in logs
           if template.startswith("TRACE download provider end")]
    assert len(end) == 1 and end[0][2] == 0
    assert private.read_bytes() == b"SECRET synthetic fixture size"


def test_provider_trace_keeps_physical_file_size_without_any_symlink(tmp_path):
    physical = tmp_path / "book.pdf"
    physical.write_bytes(b"%PDF-1.7\nsynthetic\n%%EOF")
    logs = []
    service = NS(acquire_digital_loan=lambda _loan: NS(
        kind="direct", path=physical,
    ))
    acquire_provider_loan(
        service, fake_loan(), force_refresh=False,
        time=time,
        _TRACE_LOG=NS(info=lambda template, *args: logs.append((template, args))),
    )
    end = [args for template, args in logs
           if template.startswith("TRACE download provider end")]
    assert len(end) == 1 and end[0][2] == physical.stat().st_size
