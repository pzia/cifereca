"""#1354: exact account/edition provenance of cache-only catalogue details.

The real SQLite mirror and Core local search are exercised without GTK,
supplier requests, holdings, fulfilment, or any rights mutation.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.known_catalog_provenance import known_catalog_search_result
from cifereca.core.search_orchestration import _mirrored_catalogue_key
from cifereca.models import SearchResult
from tests.test_catalog_083 import _service


def notice(*, credential="reader-A", docbase="EDITION-1",
           collection="catalogue-A", identity="default"):
    return SearchResult(
        id=f"paris:{credential}:{docbase}:{collection}",
        record_id="same-supplier-record", docbase=docbase, medium="digital",
        title="Le livre partagé", author="Auteur fictif", type="ebook",
        format="EPUB", publisher="Éditeur", published_at=None,
        description="Deux éditions portant le même identifiant amont",
        thumbnail_url=None, friendly_url="https://example.invalid/notice",
        scenario="PNB", source_provider="paris", source_credential=credential,
        source_collection=collection, owned=None, downloadable=None,
    )


def test_same_remote_id_across_credentials_docbases_collections_never_overwrites(
    tmp_path,
):
    service = _service(str(tmp_path))
    try:
        base = notice()
        other_account = notice(credential="reader-B")
        other_edition = notice(docbase="EDITION-2")
        other_collection = notice(collection="catalogue-B")
        versions = [base, other_account, other_edition, other_collection]
        keys = [_mirrored_catalogue_key("default", row)[0] for row in versions]
        assert len(set(keys)) == len(versions)
        assert all(_mirrored_catalogue_key("default", row)[1] for row in versions)
        assert service._mirror_remote_results(versions, identity_id="default") == 4
        # Repeated imports update only their OWN account+edition row.
        assert service._mirror_remote_results([base], identity_id="default") == 1
        saved = service.storage.catalog_entries(identity_id="default", provider_id="paris")
        assert len(saved) == 4
        assert {r["catalog_entry_id"] for r in saved} == set(keys)

        matches = service.search_known_catalog(
            "livre partagé", identity_id="default", provider_ids=["paris"],
        )
        assert len(matches) == 4
        assert len({m["key"] for m in matches}) == 4
        assert {
            (m["metadata"]["source_credential"], m["metadata"]["docbase"],
             m["metadata"]["source_collection"]) for m in matches
        } == {
            ("reader-A", "EDITION-1", "catalogue-A"),
            ("reader-B", "EDITION-1", "catalogue-A"),
            ("reader-A", "EDITION-2", "catalogue-A"),
            ("reader-A", "EDITION-1", "catalogue-B"),
        }
        assert all(m["metadata"]["catalogue_scope_version"] == 2 for m in matches)
        for row in matches:
            detail = known_catalog_search_result(row, identity_id="default")
            assert detail is not None
            assert detail.source_provider == "paris"
            assert detail.source_credential == row["metadata"]["source_credential"]
            assert detail.docbase == row["metadata"]["docbase"]
            assert (detail.source_collection or "") == row["metadata"]["source_collection"]
            assert detail.owned is None and detail.downloadable is None
            assert detail.download_url is None and detail.friendly_url is None
        assert service.storage.entitlements() == []
        assert service.storage.acquisitions() == []
        assert service.search_known_catalog("livre partagé", identity_id="alex") == []
    finally:
        service.close()


@pytest.mark.parametrize("bad", [
    "historical", "missing_credential", "missing_docbase", "unknown_collection",
    "changed_identity", "wrong_record", "other_source", "stale", "legacy_raw",
])
def test_unsupported_or_ambiguous_historic_row_cannot_offer_actions(tmp_path, bad):
    service = _service(str(tmp_path))
    try:
        service._mirror_remote_results([notice()], identity_id="default")
        row, = service.search_known_catalog("livre partagé", identity_id="default")
        row = dict(row)
        meta = dict(row["metadata"])
        row["metadata"] = meta
        identity = "default"
        if bad == "historical":
            meta.pop("catalogue_scope_version")
        elif bad == "missing_credential":
            meta["source_credential"] = ""
        elif bad == "missing_docbase":
            meta["docbase"] = None
        elif bad == "unknown_collection":
            meta["source_collection"] = ["catalogue-A", "catalogue-B"]
        elif bad == "changed_identity":
            identity = "alex"
        elif bad == "wrong_record":
            row["item_id"] = ""
        elif bad == "other_source":
            row["source"] = "library"
        elif bad == "stale":
            meta["search_provenance"] = "known_catalog_stale"
        elif bad == "legacy_raw":
            meta.pop("catalogue_scope_version")
            meta["search_result"] = {
                "source_provider": "paris", "source_credential": "reader-B",
                "docbase": "EDITION-2",
            }
        assert known_catalog_search_result(row, identity_id=identity) is None
    finally:
        service.close()


def test_unknown_or_legacy_scope_remains_searchable_but_never_actionable(tmp_path):
    service = _service(str(tmp_path))
    try:
        incomplete = replace(notice(), source_credential="", docbase="")
        key, qualified = _mirrored_catalogue_key("default", incomplete)
        assert key and not qualified
        service._mirror_remote_results([incomplete], identity_id="default")
        found, = service.search_known_catalog("livre partagé", identity_id="default")
        assert found["metadata"]["catalogue_scope_version"] is None
        assert known_catalog_search_result(found, identity_id="default") is None
        # A historical catalogue notice remains visible in local Search.
        assert found["title"] == "Le livre partagé"
    finally:
        service.close()


def test_core_gui_gate_requires_current_exact_identity_and_never_replays_raw():
    # Headless runners have no gi. Inspect only the thin GTK facade boundary;
    # the actual reconstruction contract above is tested through Core.
    from pathlib import Path
    from tests.gui_source import gui_source_text
    src = gui_source_text(Path("src/cifereca/gui/main_window.py"))
    assert "known_catalog_search_result(row, identity_id=identity)" in src
    assert "known_catalog_identity_id" in src
    assert "SearchResult(**raw)" not in src
