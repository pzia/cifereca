"""#4048: Paris no longer publishes or executes voluntary loan returns.

Safe, synthetic tests: no credential, HTTP request, token, ADEPT, SQLite,
or opening of a real borrower session. Live inventory and physical renewal
remain separate read/action contracts.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca.providers.base import ProviderSession
from cifereca_paris import DESCRIPTOR, ParisProviderSession
from cifereca_paris_client import ParisClient


ROOT = Path(__file__).resolve().parents[1]


def test_paris_return_transport_deleted_not_merely_hidden_from_gtk():
    source = (ROOT / "cifereca_paris_client.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    client = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "ParisClient")
    methods = {node.name for node in client.body if isinstance(node, ast.FunctionDef)}
    assert "return_digital_loan" not in methods
    assert "ReturnDigitalLoan" not in source
    assert "renew_loan" in methods
    assert "loans" in methods
    assert "history" in methods
    assert not hasattr(ParisClient, "return_digital_loan")


def test_paris_retired_return_dispatch_falls_back_to_nonmutating_base():
    assert DESCRIPTOR.capabilities.loans is True
    assert DESCRIPTOR.capabilities.renew_loan is True
    assert DESCRIPTOR.capabilities.return_loan is False
    assert ParisProviderSession.return_digital is ProviderSession.return_digital

    # A stale Core or old GUI may still call the ProviderSession entrypoint.
    # It must fail before a network/account access, regardless of loan data.
    provider = object.__new__(ParisProviderSession)
    provider.provider_id = "paris"
    calls = []

    class PoisonTransport:
        def __getattr__(self, name):
            calls.append(name)
            raise AssertionError("Unexpected HTTP access")

    provider.client = PoisonTransport()
    for account in (SimpleNamespace(unique_id="", is_primary=True),
                    SimpleNamespace(unique_id="linked-1", is_primary=False)):
        with pytest.raises(ProviderCapabilityError, match="return unsupported"):
            provider.return_digital(
                SimpleNamespace(id="synthetic", provider="DILICOM", can_return=True),
                account,
            )
    assert calls == []


def test_paris_package_never_advertises_voluntary_return_but_keeps_renewal():
    manifest = json.loads((ROOT / "cifereca-plugin.json").read_text(encoding="utf-8"))
    plugin, = manifest["plugins"]
    assert plugin["version"] == manifest["package"]["version"] == "1.2.41"
    assert "lending.return" not in plugin["capabilities"]
    assert "loan.return" not in plugin["capabilities"]
    assert "sync.loans" in plugin["capabilities"]
    assert "acquisition.borrow" in plugin["capabilities"]
    assert "acquisition.download" in plugin["capabilities"]
    assert DESCRIPTOR.capabilities.digital_download is True
    assert DESCRIPTOR.capabilities.renew_loan is True
