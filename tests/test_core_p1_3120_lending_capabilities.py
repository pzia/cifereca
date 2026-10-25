"""#3120: public loan actions require matched manifest, method and live account."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from cifereca.models import Account
from cifereca.providers.base import ProviderCapabilities, ProviderDescriptor, ProviderSession
from cifereca.plugin_api.lending_capabilities import assess_lending_operation

ROOT = Path(__file__).resolve().parents[1]


def account(provider="paris", credential="card-1", borrower="", identity="alice"):
    return Account(
        provider_id=provider, credential_id=credential,
        identity_id=identity, unique_id=borrower,
    )


def descriptor(provider="paris", *, loans=True, borrow=False, digital=False,
               renew=False, ret=False, state="ready"):
    return ProviderDescriptor(
        id=provider, display_name="Synthetic lending source", description="Fixture",
        state=state,
        capabilities=ProviderCapabilities(
            loans=loans, borrow=borrow, digital_download=digital,
            renew_loan=renew, return_loan=ret,
        ),
    )


class OnlyList(ProviderSession):
    def __init__(self, desc, row):
        super().__init__(
            descriptor=desc, credential_id=row.credential_id, label="Fixture",
            credentials={}, options={},
        )
        self._accounts = [row]
        self.connected = True

    def connect(self):
        raise AssertionError("Capability inspection must never connect")

    def close(self):
        self.connected = False

    def loans(self, row):
        raise AssertionError("Capability inspection must never read loans")


class ParisActions(OnlyList):
    def borrow(self, *args):
        raise AssertionError("Capability inspection must never borrow")

    def renew(self, *args):
        raise AssertionError("Capability inspection must never renew")

    def return_digital(self, *args):
        raise AssertionError("Capability inspection must never return")

    def download_digital(self, *args):
        raise AssertionError("Capability inspection must never download")


class BnfInternalToken(OnlyList):
    def download_digital(self, *args):
        raise AssertionError("Internal ACSM staging must never run")


def manifest(provider):
    path = ROOT / "src" / "plugins" / (
        "provider-paris" if provider == "paris" else "provider-bnf-pret"
    ) / "cifereca-plugin.json"
    return json.loads(path.read_text(encoding="utf-8"))["plugins"][0]["capabilities"]


def fact(op, d, s, a, *, capabilities=None):
    if capabilities is None:
        capabilities = manifest(d.id)
    return assess_lending_operation(
        op, manifest_capabilities=capabilities,
        descriptor=d, session=s, account=a,
    )


def test_paris_claims_list_borrow_and_download_but_still_needs_explicit_consent():
    a = account()
    d = descriptor(borrow=True, digital=True, renew=True, ret=True)
    s = ParisActions(d, a)
    for op in ("loan.list", "loan.borrow", "loan.download_token"):
        r = fact(op, d, s, a)
        assert r.availability == "supported"
        assert r.reason_code == "available_unverified_permission"
        assert r.usable
        assert r.explicit_action_required is (op != "loan.list")
    # Legacy v1 manifest does not declare separate return/renew operations.
    # Keep the old actual provider untouched, but do not over-advertise it.
    for op in ("loan.return", "loan.renew"):
        r = fact(op, d, s, a)
        assert r.availability == "unknown"
        assert r.reason_code == "legacy_capability_unmapped"
        assert not r.usable


def test_bnf_internal_download_method_is_not_a_public_capability():
    a = account("bnf-pret", "bnf-card", "reader")
    d = descriptor(provider="bnf-pret")
    s = BnfInternalToken(d, a)
    assert fact("loan.list", d, s, a).availability == "supported"
    for op in ("loan.borrow", "loan.return", "loan.renew", "loan.download_token"):
        r = fact(op, d, s, a)
        assert r.availability in {"unsupported", "unknown"}
        assert not r.usable


def test_manifest_descriptor_and_method_must_all_agree():
    a = account()
    claimed = descriptor(loans=True)
    unimplemented = ProviderSession
    s = OnlyList(claimed, a)
    assert fact("loan.list", descriptor(loans=False), s, a).reason_code == (
        "manifest_descriptor_conflict"
    )
    # Manifest + descriptor can both claim a method inherited unchanged
    # from ProviderSession, but it is still unsupported.
    assert fact("loan.borrow", descriptor(borrow=True), s, a).reason_code == (
        "method_unimplemented"
    )
    # An implementation without declaration does not publish a new action.
    assert fact("loan.borrow", descriptor(borrow=False), ParisActions(claimed, a),
                a, capabilities=("sync.loans",)).availability == "unsupported"


@pytest.mark.parametrize("mutation", ["disconnected", "wrong_credential", "other_identity",
                                       "unrecognized_borrower", "plugin_not_ready"])
def test_session_account_and_plugin_readiness_fail_closed(mutation):
    a = account()
    d = descriptor()
    s = OnlyList(d, a)
    if mutation == "disconnected":
        s.connected = False
    elif mutation == "wrong_credential":
        s.credential_id = "other-card"
    elif mutation == "other_identity":
        s._accounts = [account(identity="bob")]
    elif mutation == "unrecognized_borrower":
        s._accounts = [account(borrower="other-linked")]
    else:
        d = replace(d, state="skeleton")
    result = fact("loan.list", d, s, a)
    assert not result.usable
    assert result.availability == "unavailable"


def test_multiple_paris_linked_accounts_remain_independently_scoped():
    a = account(borrower="")
    linked = account(borrower="reader-2")
    d = descriptor()
    s = OnlyList(d, a)
    s._accounts.append(linked)
    assert fact("loan.list", d, s, linked).usable
    stranger = account(borrower="reader-3")
    assert not fact("loan.list", d, s, stranger).usable


def test_invalid_manifest_unknown_operation_and_missing_account_never_promote_action():
    a = account()
    d = descriptor()
    s = OnlyList(d, a)
    for caps in (None, 123, ("sync.loans", "sync.loans"),
                 ("sync.loans", object())):
        r = assess_lending_operation(
            "loan.list", manifest_capabilities=caps,
            descriptor=d, session=s, account=a,
        )
        assert r.availability == "unknown" and not r.usable
    r = fact("loan.list", d, s, None)
    assert not r.usable
    r = fact("loan.acquire", d, s, a)
    assert r.operation == "unknown" and not r.usable
