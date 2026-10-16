"""#1917: Paris public catalogue diagnostics never publish upstream errors.

No live HTTP, login, loan mutation, download or private account is used.
"""
from types import SimpleNamespace

import pytest

from cifereca_paris_client import ParisClient, ParisError, ParisSearchUnavailable


SECRET = "PRIVATE_SIGNED_URL?token=PRIVATE_ACCESS_TOKEN"


class Diagnostics:
    def __init__(self):
        self.rows = []

    def add(self, *args, **kwargs):
        self.rows.append({"args": args, "kwargs": kwargs})


def make_client():
    diagnostics = Diagnostics()
    client = ParisClient("https://example.test", diagnostics=diagnostics)
    return client, diagnostics


def test_recovered_syracuse_retry_does_not_export_exception_message():
    client, diagnostic = make_client()
    sizes = []

    def scenario(_query, _name, _page, result_size):
        sizes.append(result_size)
        if result_size == 50:
            raise ParisError(SECRET)
        return [SimpleNamespace(id="public-1", title="Livre public")]

    client._search_scenario = scenario
    try:
        rows = client.search("titre", scope="digital")
        assert [row.id for row in rows] == ["public-1"]
        assert sizes == [50, 20]
        assert client.last_search_warnings == []
        assert len(diagnostic.rows) >= 3
        exported = repr(diagnostic.rows)
        for forbidden in ("PRIVATE_", "token=", "SIGNED_URL"):
            assert forbidden not in exported
        failure = diagnostic.rows[0]["args"][-1]
        assert failure == {"error": "ParisError", "code": "service_unavailable"}
        retry = next(
            row for row in diagnostic.rows
            if row["args"][:3] == ("CATALOG", "search EBOOKS fallback", "retry")
        )
        assert retry["kwargs"]["detail"] == {
            "result_size": 20,
            "error": "ParisError",
            "code": "service_unavailable",
        }
    finally:
        client.close()


def test_partial_public_results_preserved_without_exporting_private_error():
    client, diagnostic = make_client()

    def scenario(_query, name, _page, _size):
        if name == "EBOOKS":
            raise ParisError(SECRET)
        return [SimpleNamespace(id="public-only", title="Livre public")]

    client._search_scenario = scenario
    try:
        rows = client.search("titre", scope="all")
        assert [row.id for row in rows] == ["public-only"]
        assert client.last_search_warnings == [
            "Catalogue numérique momentanément indisponible"
        ]
        assert "PRIVATE_" not in repr(diagnostic.rows)
        assert "SIGNED_URL" not in repr(diagnostic.rows)
    finally:
        client.close()


def test_all_branch_failures_return_safe_error_and_no_raw_diagnostics():
    client, diagnostic = make_client()
    client._search_scenario = (
        lambda *_: (_ for _ in ()).throw(ParisError(SECRET))
    )
    try:
        with pytest.raises(ParisSearchUnavailable) as exc:
            client.search("titre", scope="digital")
        assert "PRIVATE_" not in str(exc.value)
        assert "token=" not in str(exc.value)
        assert "PRIVATE_" not in repr(diagnostic.rows)
        assert len(diagnostic.rows) >= 3
    finally:
        client.close()


def test_history_cover_search_diagnostic_is_redacted_too():
    client, diagnostic = make_client()
    client._search_scenario = (
        lambda *_: (_ for _ in ()).throw(ParisError(SECRET))
    )
    try:
        with pytest.raises(ParisError):
            client.search_catalog("titre")
        assert "PRIVATE_" not in repr(diagnostic.rows)
        assert diagnostic.rows[0]["args"][-1]["code"] == "service_unavailable"
    finally:
        client.close()
