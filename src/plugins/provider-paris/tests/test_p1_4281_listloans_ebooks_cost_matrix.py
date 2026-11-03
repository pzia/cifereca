"""P1 #4281 — Paris ListLoans / optional EBOOKS cost matrix, offline.

The measured unit here is a *request count*, not a wall-clock latency or
proof of supplier performance. Do not infer a cache of rights from these
descriptive-only EBOOKS lookups.
"""
from __future__ import annotations

from collections import Counter

import httpx
import pytest

from tests.test_p1_3222_paris_listloans_http import _fixture
from tests.test_p1_4157_listloans_completeness_matrix import loan_row, reply


@pytest.mark.parametrize("digital,physical", [
    (0, 0), (1, 0), (4, 0), (20, 0), (0, 1), (4, 3),
])
def test_paris_listloans_and_optional_ebooks_lookup_cost_at_cold_and_warm_read(
    digital, physical,
):
    """Two authoritative GETs on every read; optional catalogue metadata cached.

    All borrower/edition IDs and responses are synthetic; no network access.
    This characterizes the *current* implementation before any optimization.
    """
    requests = []
    def handler(request):
        assert request.method == "GET"
        assert request.url.path.endswith("/ListLoans")
        branch = request.url.params.get("serviceCode")
        assert branch in {"DILICOM", "SYRACUSE"}
        requests.append((branch, request.url.params.get("userUniqueIdentifier")))
        rows = (
            tuple(loan_row(f"d{n}") for n in range(digital))
            if branch == "DILICOM"
            else tuple(loan_row(f"p{n}") for n in range(physical))
        )
        return reply(rows)

    session, account, _diagnostics = _fixture(handler)
    queries = []
    def search(query, *, scenario, result_size):
        assert scenario == "EBOOKS"
        assert result_size == 12
        queries.append(query)
        return []
    session.client.search_catalog = search
    try:
        cold = session.loan_inventory_snapshot(account)
        assert cold.status == "success"
        assert len(cold.loans) == digital + physical
        assert Counter(branch for branch, _ in requests) == {
            "DILICOM": 1, "SYRACUSE": 1,
        }
        assert all(borrower == account.unique_id for _, borrower in requests)
        # One exact edition lookup + one title fallback per un-enriched loan.
        assert len(queries) == 2 * digital
        assert all(q for q in queries)

        # A second *explicit* inventory is still a fresh two-branch read.
        # Only descriptive EBOOKS metadata, not loan rights, may be reused.
        warm = session.loan_inventory_snapshot(account)
        assert warm.status == "success"
        assert len(warm.loans) == digital + physical
        assert Counter(branch for branch, _ in requests) == {
            "DILICOM": 2, "SYRACUSE": 2,
        }
        assert len(queries) == 2 * digital
    finally:
        session.close()


@pytest.mark.parametrize("failed_branch", ["DILICOM", "SYRACUSE"])
def test_paris_one_failed_branch_keeps_other_branch_without_retry_or_cross_read(
    failed_branch,
):
    requests = []
    def handler(request):
        branch = request.url.params.get("serviceCode")
        requests.append(branch)
        if branch == failed_branch:
            return httpx.Response(503, json={"success": False})
        return reply((loan_row("one"),))

    session, account, _diagnostics = _fixture(handler)
    searches = []
    session.client.search_catalog = lambda *args, **kwargs: searches.append(args) or []
    try:
        snapshot = session.loan_inventory_snapshot(account)
        assert snapshot.status == "partial"
        assert requests == ["DILICOM", "SYRACUSE"]
        assert len(snapshot.loans) == 1
        assert {b.upstream: b.completeness for b in snapshot.branches}[
            failed_branch.lower()
        ] == "partial"
        # A physical-only successful branch does not trigger EBOOKS.
        assert len(searches) == (2 if failed_branch == "SYRACUSE" else 0)
    finally:
        session.close()
