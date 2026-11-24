"""#4030 explicit purchase read: bounds, omissions and same-read scope races."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core import local_origin_remote_purchases as remote
from cifereca.core.local_origin_suggestion_operator import run_local_origin_suggestion
from cifereca.plugin_api import (
    PurchaseEnrichmentCandidate, PurchaseEnrichmentObservation,
    PurchaseEnrichmentOmission, from_legacy_rights_snapshot,
)
from tests.test_core_p0_4030_private_catalogue import private_rig, rig, store, new_source


def _image(storage):
    with storage._lock, storage._connect() as db:
        return tuple(tuple(db.execute("SELECT * FROM " + table).fetchall())
                     for table in ("artifacts", "placements", "catalog_entries",
                                   "source_references", "origin_links", "entitlements",
                                   "acquisition_ledger", "backup_items", "metadata", "cache"))


@pytest.mark.parametrize("case", ["homonyms", "truncated", "omitted", "malformed",
                                  "session_swap", "provider_error", "complete"])
def test_remote_purchase_window_retains_no_authority_or_private_locator(
    private_rig, tmp_path, monkeypatch, caplog, case,
):
    service, request, account, session, calls, path, *_ = private_rig
    session.capabilities = NS(personal_library=True)
    _, first = new_source()
    _, second = new_source(edition="other-edition", right="other-purchase")
    snapshot = replace(first, rights=first.rights + second.rights)
    if case == "complete":
        snapshot = replace(snapshot, status="success", coverage="complete",
                           pagination_complete=True, observed_at=first.rights[0].observed_at,
                           snapshot_ref="fixture-window")
    inventory = from_legacy_rights_snapshot(snapshot)
    locator = "https://catalog.example.org/product/123"
    candidates = tuple(PurchaseEnrichmentCandidate(
        item.source, locator + str(index), "Roman commun", item.observed_at)
        for index, item in enumerate(inventory.resources))
    if case == "truncated":
        monkeypatch.setattr(remote, "_MAX_MATCHES", 1)
    secret = "https://private.example/?token=fixture-secret"

    def reader(selected):
        assert selected is account
        calls.append("purchase_observation")
        if case == "provider_error":
            raise RuntimeError(secret)
        if case == "omitted":
            return PurchaseEnrichmentObservation(inventory, omissions=tuple(
                PurchaseEnrichmentOmission(item.source, item.observed_at,
                                           "direct_locator_unattested")
                for item in inventory.resources))
        response = PurchaseEnrichmentObservation(inventory, candidates)
        if case == "malformed":
            object.__setattr__(candidates[0], "public_locator", secret)
        elif case == "session_swap":
            service._sessions["private"] = NS(**vars(session))
        return response

    session.purchase_enrichment_observation = reader
    session.rights_observation_snapshot = lambda *_a: pytest.fail("no fallback")
    session.library = session.download = session.search = lambda *_a: pytest.fail("no extra IO")
    monkeypatch.setattr(remote, "acquisition_lock_file", lambda: tmp_path / "acquire.lock")
    known = run_local_origin_suggestion(service, request)
    choice, = remote.available_remote_purchase_catalogues(
        service, request, evidence=known["public_comparison_evidence"])
    before, contents = _image(service.storage), path.read_bytes()
    assert not calls
    result = remote.compare_remote_purchase_catalogue(service, choice, confirmed=True)
    assert calls == ["purchase_observation"]
    expected = {"homonyms": 2, "truncated": 1, "complete": 2}.get(case, 0)
    assert len(result["candidates"]) == expected
    if expected:
        assert result["status"] == "partial"
        assert len({r.edition_id for r in result["candidates"]}) == expected
        assert all(r.comparison == "title_concordant_author_unknown" for r in result["candidates"])
    assert result["window_truncated"] == (case == "truncated")
    assert not any(result[k] for k in ("coverage_complete", "fresh_network_verified",
                                       "association_authorized", "may_write_origin_or_right",
                                       "local_description_transmitted"))
    assert locator not in repr(result) and secret not in repr(result) and secret not in caplog.text
    assert _image(service.storage) == before and path.read_bytes() == contents
