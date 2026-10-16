"""#1917: Paris' public search must not convert a valid empty branch to outage.

No real Syracuse requests, borrower accounts, loans or DRM are involved.
"""
import httpx
import pytest

from cifereca_paris_client import ParisClient, ParisError, ParisSearchUnavailable


class Diagnostics:
    def __init__(self):
        self.rows = []

    def add(self, *args, **kwargs):
        self.rows.append((args, kwargs))


def session():
    diagnostics = Diagnostics()
    client = ParisClient("https://example.test", diagnostics=diagnostics)
    return client, diagnostics


@pytest.mark.parametrize("healthy", ("CATALOGUE", "EBOOKS"))
def test_healthy_empty_branch_plus_broken_sibling_is_partial_zero(healthy):
    client, diagnostics = session()
    calls = []

    def fake_search(query, scenario, page, size):
        calls.append((scenario, size))
        if scenario == healthy:
            return []
        raise ParisError("PRIVATE_SIGNED_URL?token=PRIVATE_ACCOUNT")

    client._search_scenario = fake_search
    try:
        assert client.search("absent", scope="all") == []
        failed = "EBOOKS" if healthy == "CATALOGUE" else "CATALOGUE"
        assert calls.count((healthy, 50)) == 1
        assert (failed, 50) in calls and (failed, 20) in calls
        assert client.last_search_warnings == [
            "partial_results : Catalogue numérique momentanément indisponible"
            if failed == "EBOOKS" else
            "partial_results : Catalogue physique momentanément indisponible"
        ]
        for args, kwargs in diagnostics.rows:
            assert "PRIVATE" not in str((args, kwargs))
            if args[0] == "CATALOG" and args[2] == "error":
                assert "message" not in args[4]
    finally:
        client.close()


def test_all_catalogues_down_is_still_a_retryable_failure():
    client, diagnostics = session()
    calls = []

    def always_fail(query, scenario, page, size):
        calls.append((scenario, size))
        raise ParisError("PRIVATE_ERROR token=SECRET")

    client._search_scenario = always_fail
    try:
        with pytest.raises(ParisSearchUnavailable) as raised:
            client.search("absent", scope="all")
        assert raised.value.code == "search_temporarily_unavailable"
        assert "PRIVATE" not in str(raised.value)
        assert len(calls) == 4
        assert "PRIVATE" not in str(diagnostics.rows)
    finally:
        client.close()


def test_an_empty_single_catalogue_is_not_a_service_failure():
    client, _ = session()
    client._search_scenario = lambda *args: []
    try:
        assert client.search("absent", scope="digital") == []
        assert client.last_search_warnings == []
    finally:
        client.close()


def test_network_error_is_reported_as_partial_without_unsafe_diagnostics():
    client, diagnostics = session()

    def fail_ebook(query, scenario, page, size):
        if scenario == "CATALOGUE":
            return []
        raise httpx.ConnectError("PRIVATE_URL?access_token=SECRET")

    client._search_scenario = fail_ebook
    try:
        assert client.search("absent", scope="all") == []
        assert client.last_search_warnings == [
            "partial_results : Catalogue numérique momentanément indisponible"
        ]
        assert "PRIVATE" not in str(diagnostics.rows)
        assert "access_token" not in str(diagnostics.rows)
    finally:
        client.close()
