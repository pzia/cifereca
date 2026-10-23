"""#2975: Paris ListLoans must fail closed on malformed negative evidence.

No credentials or network. Both upstream branches use the same response guard.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import httpx
import pytest


class _FakeHTTP:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return httpx.Response(
            200, json=self.payload,
            request=httpx.Request("GET", "https://example.invalid/ListLoans"),
        )

    def close(self):
        pass


def _read(monkeypatch, payload, upstream="DILICOM"):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "src" / "plugins" / "provider-paris"))
    module = importlib.import_module("cifereca_paris_client")
    client = _FakeHTTP(payload)
    adapter = module.ParisClient(client_factory=lambda: client)
    try:
        return adapter.loans(upstream, "reader")
    finally:
        adapter.close()


@pytest.mark.parametrize("upstream", ["DILICOM", "SYRACUSE"])
def test_valid_explicit_empty_list_is_preserved(monkeypatch, upstream):
    assert _read(monkeypatch, {"success": True, "d": {"Loans": []}}, upstream) == []


@pytest.mark.parametrize("payload", [
    {"success": True},
    {"success": True, "d": None},
    {"success": True, "d": {}},
    {"success": True, "d": []},
    {"success": True, "d": {"Loans": None}},
    {"success": True, "d": {"Loans": {"Loan": []}}},
    {"success": True, "d": {"Loans": "private-SECRET"}},
    {"success": True, "d": {"Loans": [{"id": "legitimate"}, None]}},
    {"success": True, "d": {"Loans": [123]}},
])
def test_malformed_list_never_masquerades_as_zero_loans(monkeypatch, payload):
    with pytest.raises(RuntimeError, match="^paris_loans_inventory_shape_invalid$") as error:
        _read(monkeypatch, payload)
    assert "SECRET" not in str(error.value)


def test_list_shape_error_does_not_depend_on_other_upstream(monkeypatch):
    payload = {"success": True, "d": {"Loans": [None]}}
    for upstream in ("DILICOM", "SYRACUSE"):
        with pytest.raises(RuntimeError, match="paris_loans_inventory_shape_invalid"):
            _read(monkeypatch, payload, upstream)
