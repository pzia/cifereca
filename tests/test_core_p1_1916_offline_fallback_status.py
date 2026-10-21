"""P1 #1916 — absence de notices ≠ limite de lecture ou cache inaccessible.

Public provider service outage is simulated without network, acquisition,
personal entitlements, or raw exception text reaching the public batch.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca.core import known_catalog_fallback
from tests.test_core043_progress_cache_stats import _service, _FakeSession
from tests.test_core_p1_1916_stale_public_fallback import _known


@pytest.mark.parametrize("mode,expected", [
    ("empty", "aucun résultat connu"),
    ("over_budget", "secours local non vérifiable"),
    ("read_error", "inventaire local de secours indisponible"),
])
@pytest.mark.parametrize("api", ["progressive", "synchronous"])
def test_outage_warning_distinguishes_absence_from_unverifiable_offline_cache(
    tmp_path, monkeypatch, mode, expected, api,
):
    service = _service(tmp_path)
    try:
        if mode in {"over_budget", "read_error"}:
            for index in range(3):
                _known(service, provider="bad", key=f"known-{index}",
                       title="Roman maritime")
        if mode == "over_budget":
            monkeypatch.setattr(known_catalog_fallback, "_MAX_CATALOG_ROWS", 2)
        elif mode == "read_error":
            def read_error(**_kwargs):
                raise OSError("PRIVATE_CATALOGUE_PATH_TOKEN")
            monkeypatch.setattr(service.storage, "catalog_entries", read_error)

        bad, good = _FakeSession("bad"), _FakeSession("good")
        attempts = []
        def unavailable(*_args, **_kwargs):
            attempts.append("offline")
            raise httpx.ReadTimeout(
                "PRIVATE_REQUEST_URI_WITH_CREDENTIAL",
            )
        bad.search = unavailable
        service._sessions = {"bad": bad, "good": good}
        if api == "progressive":
            batches = []
            summary = service.search_progressive(
                "Roman maritime", field="title", force=True,
                on_batch=batches.append,
            )
            affected = next(row for row in batches if row.provider_id == "bad")
            warnings = affected.warnings
            assert affected.results == ()
            assert affected.error_code == "SearchTemporarilyUnavailable"
            assert summary.providers_failed == 1
            assert summary.result_count == 1  # good source remains visible
        else:
            results = service.search("Roman maritime", field="title", force=True)
            assert len(results) == 1
            assert all(row.source_provider != "bad" for row in results)
            warnings = service.last_search_warnings

        assert len(attempts) == 1
        assert expected in str(warnings)
        if mode != "empty":
            assert "aucun résultat connu" not in str(warnings)
        assert "PRIVATE" not in str(warnings)
        assert "PRIVATE" not in repr(service.last_search)
    finally:
        service.close()


def test_status_helper_exposes_only_closed_constants():
    from cifereca.core.search_orchestration import _stale_public_fallback_warning
    known = _stale_public_fallback_warning("no_known_results")
    budget = _stale_public_fallback_warning("scope_budget_exceeded")
    outage = _stale_public_fallback_warning("unavailable")
    assert "aucun résultat connu" in known
    assert "non vérifiable" in budget
    assert "indisponible" in outage
    assert "PRIVATE_TOKEN" not in str([
        _stale_public_fallback_warning("PRIVATE_TOKEN"),
        budget, outage,
    ])
