"""P1 #4281 — rights observations must never invoke descriptive EBOOKS search.

Synthetic Paris session and Core owner, no HTTP, loan POST or rights cache.
Fresh DILICOM/SYRACUSE inventory is still required for *each* observation.
"""
from __future__ import annotations

import logging

import pytest

from cifereca_paris import ParisProviderSession
from tests.test_p1_3252_rights_observation_snapshot import fixture, upstream


@pytest.mark.parametrize("count", [0, 1, 4, 20])
def test_rights_read_skips_optional_ebooks_but_keeps_fresh_two_branch_inventory(
    count, caplog,
):
    rows = tuple(upstream(f"loan-{n}", f"edition-{n}") for n in range(count))
    session, account, client = fixture(digital=rows)
    # The rights fixture normally replaces this method with a no-op to test
    # only claim normalization. Here exercise the real descriptive path.
    session._enrich_digital_loan_metadata = (
        ParisProviderSession._enrich_digital_loan_metadata.__get__(session)
    )
    search_calls = []
    def search(query, *, scenario, result_size):
        assert scenario == "EBOOKS" and result_size == 12
        search_calls.append(query)
        return []
    client.search_catalog = search
    caplog.set_level(logging.INFO, logger="cifereca")
    try:
        first = session.rights_observation_snapshot(account)
        assert first.status == "success"
        assert first.coverage == "complete"
        assert len(first.rights) == count
        assert search_calls == []
        assert client.calls == [
            ("DILICOM", account.unique_id), ("SYRACUSE", account.unique_id),
        ]
        assert not session._loan_catalog_metadata_cache

        # The UI/inventory reader still enriches descriptions when requested.
        inventory = session.loan_inventory_snapshot(account)
        assert inventory.status == "success"
        assert len(inventory.loans) == count
        assert len(search_calls) == 2 * count
        assert len(client.calls) == 4

        # A subsequent rights reading remains fresh, not cached or inferred.
        again = session.rights_observation_snapshot(account)
        assert again.status == "success"
        assert len(again.rights) == count
        assert len(client.calls) == 6
        assert len(search_calls) == 2 * count

        messages = [
            record.getMessage() for record in caplog.records
            if "TRACE paris list-loans timing phase=metadata" in record.getMessage()
        ]
        assert len(messages) == 3
        assert "consumer=rights_observation" in messages[0]
        assert "ebooks_attempted=0" in messages[0]
        assert "consumer=loan_inventory" in messages[1]
        assert f"ebooks_attempted={2 * count}" in messages[1]
        assert "consumer=rights_observation" in messages[2]
        assert "ebooks_attempted=0" in messages[2]
    finally:
        session.close()


def test_unavailable_dilicom_rights_never_fall_back_to_descriptive_ebooks():
    session, account, client = fixture(
        digital=(upstream("loan-1", "edition-1"),),
        physical=(upstream("physical-1", "edition-2"),),
    )
    session._enrich_digital_loan_metadata = (
        ParisProviderSession._enrich_digital_loan_metadata.__get__(session)
    )
    searched = []
    client.search_catalog = lambda *args, **kwargs: searched.append(args) or []
    original = client.loans

    def broken_dilicom(upstream_name, borrower):
        if upstream_name == "DILICOM":
            raise RuntimeError("synthetic upstream unavailable")
        return original(upstream_name, borrower)

    client.loans = broken_dilicom
    try:
        rights = session.rights_observation_snapshot(account)
        assert rights.status == "failed"
        assert rights.coverage == "unknown"
        assert not rights.rights
        assert searched == []
        # One physical GET is still admitted; no extra digital GET is forged.
        assert client.calls == [("SYRACUSE", account.unique_id)]
    finally:
        session.close()
