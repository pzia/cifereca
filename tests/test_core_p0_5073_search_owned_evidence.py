"""#5518/#5073: neither provider flags nor legacy rights publish ownership."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.query_model import result_row
from tests.test_search_results_view import _book, _host


def _item(provider="fictional-provider", **changes):
    item = replace(
        _book(provider, record="edition-A"),
        source_credential="credential-A", source_account_id="account-A",
        source_collection="library", revision_id="revision-A", owned=True,
    )
    return replace(item, **changes)


def _old_proof(provider="fictional-provider"):
    return NS(identity_id="reader-A", schema_version=1,
              rights=frozenset({(provider, "credential-A", "account-A", "edition-A", "revision-A")}),
              account_context_epoch=7, source_context_epoch=9)


@pytest.mark.parametrize("provider", ["fictional-provider", "kobo", "numilog", "paris"])
@pytest.mark.parametrize("owned", [True, False, None, 1])
def test_supplier_flag_and_exact_historical_proof_cannot_mark_owned(provider, owned):
    row = result_row(_item(provider, owned=owned), purchase_evidence=_old_proof(provider),
                     identity_id="reader-A", account_context_epoch=7, source_context_epoch=9)
    assert row["state"] == "remote_catalog" and row["owned"] is (None if owned is None else False)


@pytest.mark.parametrize("changes", [
    {"source_provider": "foreign-provider"},
    {"source_credential": "foreign-credential"},
    {"source_credential": "__public__"},
    {"source_account_id": "foreign-account"},
    {"source_account_id": None},
    {"record_id": "foreign-edition"},
    {"revision_id": "foreign-revision"},
    {"revision_id": None},
    {"source_collection": "public_catalog"},
    {"source_collection": "catalog"},
    {"metadata": {"search_provenance": "known_catalog_stale"}},
    {"metadata": {"owned": True, "right_id": "fake", "identity_id": "reader-A"}},
])
def test_foreign_or_stale_source_is_descriptive_even_with_old_proof(changes):
    item = replace(_item(), **changes)
    row = result_row(item, purchase_evidence=_old_proof(), identity_id="reader-A")
    assert row["state"] == "remote_catalog" and row["owned"] is False


def test_search_batch_never_reads_ownership_ledger():
    host = _host()
    def forbidden(*args, **kwargs):
        pytest.fail("remote notices must not read an ownership ledger")
    host.service.storage = NS(entitlements=forbidden, source_references=forbidden)
    host.service.can_direct_download = Mock(return_value=False)
    host._remote_search_results = [_item()]
    host._populate_search(host._remote_search_results, announce=False)
    row = host.search_page.show_rows.call_args.args[0][0]
    assert "Mes achats" not in row.meta
    assert "Notice de bibliothèque" in row.meta
    assert row.options["action_label"] is None
