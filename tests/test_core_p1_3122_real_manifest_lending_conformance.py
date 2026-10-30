"""P1 #3122: static conformance of *real* Paris and BnF plugin packages.

This inspects current source descriptors, session classes and manifests,
without constructing sessions, authenticating or making HTTP/ACSM requests.
Fixtures in test_core_p1_3122_paris_bnf_snapshots cover the separate I/O seam.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cifereca.plugin_api.lending_capabilities import validate_lending_publication
from cifereca.providers.base import ProviderSession

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIRS = {
    "paris": ROOT / "src" / "plugins" / "provider-paris",
    "bnf-pret": ROOT / "src" / "plugins" / "provider-bnf-pret",
}
for directory in PLUGIN_DIRS.values():
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from cifereca_paris import DESCRIPTOR as PARIS_DESCRIPTOR, ParisProviderSession
from cifereca_bnf_pret import DESCRIPTOR as BNF_DESCRIPTOR, BnfProviderSession


def manifest(provider):
    return json.loads(
        (PLUGIN_DIRS[provider] / "cifereca-plugin.json").read_text(
            encoding="utf-8",
        )
    )


def publication(provider, descriptor, session_type):
    info = manifest(provider)
    plugin, = info["plugins"]
    return {
        fact.operation: fact
        for fact in validate_lending_publication(
            manifest_capabilities=plugin["capabilities"],
            descriptor=descriptor, session_type=session_type,
        )
    }


@pytest.mark.parametrize("provider,descriptor,session_type", [
    ("paris", PARIS_DESCRIPTOR, ParisProviderSession),
    ("bnf-pret", BNF_DESCRIPTOR, BnfProviderSession),
])
def test_real_packages_publish_the_same_provider_id_version_and_lending_api(
    provider, descriptor, session_type,
):
    info = manifest(provider)
    plugin, = info["plugins"]
    assert plugin["id"] == "provider." + provider
    assert plugin["type"] == "provider.library"
    assert plugin["version"] == info["package"]["version"]
    # Both published provider manifests now require Plugin API >=1.4.1.
    # Keep this static conformance fixture aligned with the actual packages.
    # Paris now requires the additive declarative account options ABI.
    expected_api = ">=1.4.3,<2" if provider == "paris" else ">=1.4.1,<2"
    assert info["package"]["plugin_api"] == expected_api
    assert "sync.loans" in plugin["capabilities"]
    assert descriptor.state == "ready" and descriptor.id == provider
    assert issubclass(session_type, ProviderSession)
    assert session_type.loan_inventory_snapshot is not ProviderSession.loans
    # Even a single positive inventory cannot prove returned loans or
    # automatically grant a new remote mutation.
    assert descriptor.capabilities.loans is True


def test_real_paris_manifest_matches_lending_methods_but_legacy_return_renew_unmapped():
    rows = publication("paris", PARIS_DESCRIPTOR, ParisProviderSession)
    assert set(rows) == {
        "loan.list", "loan.borrow", "loan.download_token",
        "loan.return", "loan.renew",
    }
    for operation in ("loan.list", "loan.borrow", "loan.download_token"):
        item = rows[operation]
        assert item.publication_status == "declared"
        assert item.publishable and item.method_implemented
    for operation in ("loan.return", "loan.renew"):
        item = rows[operation]
        assert item.publication_status == "legacy_unmapped"
        assert item.method_implemented and item.descriptor_declared
        assert not item.manifest_declared and not item.publishable
    assert PARIS_DESCRIPTOR.capabilities.opening_loan_auto_acquisition is True


def test_real_bnf_internal_acsm_does_not_become_public_download_or_mutation():
    rows = publication("bnf-pret", BNF_DESCRIPTOR, BnfProviderSession)
    assert rows["loan.list"].publishable
    for operation in (
        "loan.borrow", "loan.return", "loan.renew", "loan.download_token",
    ):
        item = rows[operation]
        assert item.publication_status == "absent"
        assert not item.publishable and not item.manifest_declared
        assert not item.descriptor_declared
    # The BnF-specific method requires a private explicit staging policy.
    # Its existence is NOT a public loan.download_token capability.
    assert rows["loan.download_token"].method_implemented
    assert BNF_DESCRIPTOR.capabilities.opening_loan_auto_acquisition is False
    assert manifest("bnf-pret")["plugins"][0]["enabled_by_default"] is False


def test_real_manifest_retains_search_read_separation_without_unverified_actions():
    paris = manifest("paris")["plugins"][0]["capabilities"]
    bnf = manifest("bnf-pret")["plugins"][0]["capabilities"]
    assert "catalog.search" in paris and "catalog.search" in bnf
    assert "catalog.detail" in paris and "catalog.detail" in bnf
    assert "acquisition.borrow" in paris and "acquisition.download" in paris
    assert "acquisition.borrow" not in bnf and "acquisition.download" not in bnf
    assert "lending.return" not in paris and "lending.renew" not in paris
    assert "lending.return" not in bnf and "lending.renew" not in bnf
