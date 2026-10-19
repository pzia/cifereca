"""#1120: catalog indexing evidence is closed and cannot execute plugin hooks."""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_provider_delivery import index_local_loan_artifact
from cifereca.providers import ProviderConfigurationError


PRIVATE = "https://private.invalid/signed.acsm?token=NEVER_LOG"


class HostileState(str):
    def __eq__(self, _other):
        raise AssertionError(PRIVATE)


class HostileInventory(dict):
    def get(self, _key, *_a):
        raise AssertionError(PRIVATE)


class HostileObject:
    def get(self, _key):
        raise AssertionError(PRIVATE)

    def __str__(self):
        raise AssertionError(PRIVATE)


@pytest.mark.parametrize("receipt", [
    {"status": HostileState("indexed"), "reason": PRIVATE},
    {"status": True, "reason": PRIVATE},
    {"status": ["indexed"], "reason": PRIVATE},
    {"status": PRIVATE, "reason": PRIVATE},
    HostileInventory(status="indexed", reason=PRIVATE),
    HostileObject(),
    None,
])
def test_index_refuses_non_native_receipt_without_leaking_or_mutating(
    tmp_path, caplog, receipt,
):
    file = tmp_path / "synthetic.epub"
    file.write_bytes(b"preserved")
    loan = NS(id="loan-1", record_id="book-1", source_provider="paris",
              source_credential="reader", source_account_id="primary",
              title="Fictional")
    calls = []
    def index(path, **options):
        calls.append((path, options))
        return receipt
    service = NS(catalog_index_path=index)
    logger = logging.getLogger("cifereca.tests.catalog-receipt")
    with caplog.at_level(logging.INFO, logger=logger.name):
        with pytest.raises(ProviderConfigurationError, match="^Indexation locale impossible$"):
            index_local_loan_artifact(
                service, loan, NS(identity_id="owner"), file,
                time=time, _TRACE_LOG=logger,
                inspect_artifact=lambda _: NS(
                    valid=True, media_format="epub", protected=False,
                ),
                ProviderConfigurationError=ProviderConfigurationError,
            )
    assert len(calls) == 1 and calls[0][0] == file
    assert calls[0][1] == {
        "title": "Fictional", "item_id": "book-1",
        "identity_id": "owner", "provider_id": "paris",
    }
    assert file.read_bytes() == b"preserved"
    assert PRIVATE not in caplog.text
    assert "indexed=False" in caplog.text


def test_native_indexed_receipt_preserves_exact_result_and_parameters(tmp_path):
    file = tmp_path / "book.pdf"
    file.write_bytes(b"%PDF-1.4\n%%EOF\n")
    loan = NS(id="loan-1", record_id="edition", source_provider="paris",
              source_credential="reader", source_account_id="primary",
              title="Fictional")
    receipt = {"status": "indexed", "artifact_id": "sha256:test"}
    inspection = NS(valid=True, media_format="pdf", protected=False)
    rows = []
    def index(path, **options):
        rows.append((path, options))
        return receipt
    result_inspection, result_receipt = index_local_loan_artifact(
        NS(catalog_index_path=index), loan, NS(identity_id="owner"), file,
        time=time, _TRACE_LOG=logging.getLogger("cifereca.tests.catalog-receipt"),
        inspect_artifact=lambda _: inspection,
        ProviderConfigurationError=ProviderConfigurationError,
    )
    assert result_inspection is inspection and result_receipt is receipt
    assert rows == [(file, {
        "title": "Fictional", "item_id": "edition",
        "identity_id": "owner", "provider_id": "paris",
    })]
