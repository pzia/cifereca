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
    digital, physical, caplog,
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
    caplog.set_level("INFO", logger="cifereca")
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
        timing = [
            record.getMessage() for record in caplog.records
            if "TRACE paris list-loans timing " in record.getMessage()
        ]
        assert len(timing) == 3
        assert sum("phase=branch consumer=loan_inventory upstream=" in line for line in timing) == 2
        assert any(
            "phase=metadata consumer=loan_inventory" in line
            and f"ebooks_attempted={2 * digital}" in line
            for line in timing
        )
        assert all(
            f"identity_id={account.identity_id}" in line
            and f"credential_id={account.credential_id}" in line
            and f"borrower_id={account.unique_id}" in line
            for line in timing
        )
        assert all("duration_ms=" in line for line in timing)

        # A second *explicit* inventory is still a fresh two-branch read.
        # Only descriptive EBOOKS metadata, not loan rights, may be reused.
        warm = session.loan_inventory_snapshot(account)
        assert warm.status == "success"
        assert len(warm.loans) == digital + physical
        assert Counter(branch for branch, _ in requests) == {
            "DILICOM": 2, "SYRACUSE": 2,
        }
        assert len(queries) == 2 * digital
        timing = [
            record.getMessage() for record in caplog.records
            if "TRACE paris list-loans timing " in record.getMessage()
        ]
        assert len(timing) == 6
        assert sum("phase=branch consumer=loan_inventory upstream=" in line for line in timing) == 4
        assert "ebooks_attempted=0" in timing[-1]  # warm descriptive cache
    finally:
        session.close()


@pytest.mark.parametrize("failed_branch", ["DILICOM", "SYRACUSE"])
def test_paris_one_failed_branch_keeps_other_branch_without_retry_or_cross_read(
    failed_branch, caplog,
):
    requests = []
    def handler(request):
        branch = request.url.params.get("serviceCode")
        requests.append(branch)
        if branch == failed_branch:
            return httpx.Response(503, json={"success": False})
        return reply((loan_row("one"),))

    session, account, _diagnostics = _fixture(handler)
    caplog.set_level("INFO", logger="cifereca")
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
        branch_logs = [
            record.getMessage() for record in caplog.records
            if "TRACE paris list-loans timing phase=branch" in record.getMessage()
        ]
        assert len(branch_logs) == 2
        assert any(
            f"upstream={failed_branch} " in line and "status=failed" in line
            and "loans=0" in line
            for line in branch_logs
        )
        assert any(
            f"upstream={({'DILICOM', 'SYRACUSE'} - {failed_branch}).pop()} " in line
            and "status=success" in line and "loans=1" in line
            for line in branch_logs
        )
    finally:
        session.close()


def test_paris_timing_marks_rights_observation_separately_from_inventory(caplog):
    from tests.test_p1_3252_rights_observation_snapshot import fixture, upstream

    session, account, client = fixture(digital=(upstream("loan-1", "edition-1"),))
    caplog.set_level("INFO", logger="cifereca")
    try:
        inventory = session.loan_inventory_snapshot(account)
        rights = session.rights_observation_snapshot(account)
        assert inventory.status == "success"
        assert rights.status == "success"
        # Both reads remain authoritative and independent; the trace only
        # attributes their costs, it does not introduce a rights cache.
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
        timing = [
            record.getMessage() for record in caplog.records
            if "TRACE paris list-loans timing " in record.getMessage()
        ]
        assert len(timing) == 6
        assert sum("consumer=loan_inventory" in line for line in timing) == 3
        assert sum("consumer=rights_observation" in line for line in timing) == 3
        assert all("identity_id=identity-A credential_id=card-A borrower_id=reader-A" in line
                   for line in timing)
        assert all("ebooks_attempted=0" in line for line in timing
                   if "phase=metadata" in line)
    finally:
        session.close()



def test_paris_cancelled_listloans_has_one_failed_scope_measure_and_no_retry(caplog):
    from cifereca.plugin_api import Cancelled

    requests = []

    def handler(request):
        requests.append(request.url.params.get("serviceCode"))
        raise Cancelled("cancelled by caller before upstream result")

    session, account, _diagnostics = _fixture(handler)
    caplog.set_level("INFO", logger="cifereca")
    try:
        with pytest.raises(Cancelled):
            session.loan_inventory_snapshot(account)
        assert requests == ["DILICOM"]
        branch_logs = [
            record.getMessage() for record in caplog.records
            if "TRACE paris list-loans timing phase=branch" in record.getMessage()
        ]
        assert len(branch_logs) == 1
        assert "upstream=DILICOM" in branch_logs[0]
        assert "status=cancelled" in branch_logs[0]
        assert "loans=0" in branch_logs[0]
        assert not any(
            "TRACE paris list-loans timing phase=metadata" in record.getMessage()
            for record in caplog.records
        )
    finally:
        session.close()
