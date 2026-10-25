"""#3120: public loan actions require matched manifest, method and live account."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from cifereca.models import Account
from cifereca.providers.base import ProviderCapabilities, ProviderDescriptor, ProviderSession
from cifereca.plugin_api.lending_capabilities import (
    assess_lending_operation, validate_lending_publication,
)

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


def _publications(d, cls, *, capabilities=None):
    if capabilities is None:
        capabilities = manifest(d.id)
    return {
        fact.operation: fact
        for fact in validate_lending_publication(
            manifest_capabilities=capabilities,
            descriptor=d, session_type=cls,
        )
    }


def test_static_paris_publication_audit_distinguishes_legacy_methods():
    d = descriptor(borrow=True, digital=True, renew=True, ret=True)
    facts = _publications(d, ParisActions)
    assert len(facts) == 5
    for action in ("loan.list", "loan.borrow", "loan.download_token"):
        assert facts[action].publication_status == "declared"
        assert facts[action].publishable
        assert facts[action].reason_code == "publication_consistent"
    for action in ("loan.renew", "loan.return"):
        assert facts[action].publication_status == "legacy_unmapped"
        assert facts[action].method_implemented
        assert not facts[action].publishable


def test_static_bnf_internal_token_is_not_promoted_to_generic_download():
    facts = _publications(descriptor("bnf-pret"), BnfInternalToken)
    assert facts["loan.list"].publishable
    token = facts["loan.download_token"]
    assert token.publication_status == "absent"
    assert token.method_implemented
    assert token.manifest_declared is False
    assert token.descriptor_declared is False
    assert not token.publishable


def test_static_validator_reports_conflicting_manifest_or_stub_without_io():
    d = descriptor(loans=True, borrow=True)
    manifest_claims = ("sync.loans", "acquisition.borrow")
    missing = _publications(d, OnlyList, capabilities=manifest_claims)
    assert missing["loan.borrow"].publication_status == "conflict"
    assert missing["loan.borrow"].reason_code == "method_unimplemented"
    contradictory = _publications(
        descriptor(borrow=False), ParisActions, capabilities=manifest_claims,
    )
    assert contradictory["loan.borrow"].publication_status == "conflict"
    assert contradictory["loan.borrow"].reason_code == "manifest_descriptor_conflict"


@pytest.mark.parametrize("bad_manifest", [
    None, 1, ("sync.loans", "sync.loans"), ("sync.loans", object()),
])
def test_static_validator_rejects_unverified_manifest_without_action(bad_manifest):
    facts = _publications(
        descriptor(), OnlyList, capabilities=bad_manifest
    ) if bad_manifest is not None else {
        fact.operation: fact
        for fact in validate_lending_publication(
            manifest_capabilities=None, descriptor=descriptor(),
            session_type=OnlyList,
        )
    }
    assert all(f.publication_status == "unverified" for f in facts.values())
    assert not any(f.publishable for f in facts.values())


def test_static_publication_never_instantiates_or_calls_provider():
    class ExplodingSession(OnlyList):
        def __init__(self, *args, **kwargs):
            raise AssertionError("publication audit must not construct a session")

        def borrow(self, *args):
            raise AssertionError("publication audit must not borrow")

    d = descriptor(borrow=True)
    facts = _publications(
        d, ExplodingSession,
        capabilities=("sync.loans", "acquisition.borrow"),
    )
    assert facts["loan.borrow"].publishable
    disabled = _publications(
        replace(d, state="skeleton"), ExplodingSession,
        capabilities=("sync.loans", "acquisition.borrow"),
    )
    assert disabled["loan.borrow"].publication_status == "unverified"
    assert not disabled["loan.borrow"].publishable


def test_static_audit_requires_real_provider_session_subclass():
    for klass in (object, None, "not a class"):
        result = validate_lending_publication(
            manifest_capabilities=("sync.loans",),
            descriptor=descriptor(), session_type=klass,
        )
        assert all(f.publication_status == "unverified" for f in result)
        assert not any(f.publishable for f in result)


# P1 #3120: a read-only capability inspection must never invoke plugin
# accounts/connected getters, method descriptors or network side effects.


def test_custom_accounts_property_must_not_execute_during_capability_read():
    a = account()
    d = descriptor()
    touched = []

    class NetworkAccounts(OnlyList):
        @property
        def accounts(self):
            touched.append("network")
            raise AssertionError("inspection must not call a plugin getter")

    s = NetworkAccounts(d, a)
    result = fact("loan.list", d, s, a)
    assert result.availability == "unavailable"
    assert not result.usable and touched == []


def test_custom_connected_property_is_not_called_if_local_session_data_exists():
    a = account()
    d = descriptor()
    touched = []

    class NetworkConnected(OnlyList):
        @property
        def connected(self):
            touched.append("network")
            raise AssertionError("inspection must not call a plugin getter")

        @connected.setter
        def connected(self, value):
            self.__dict__["connected"] = value

    s = NetworkConnected(d, a)
    assert fact("loan.list", d, s, a).availability == "supported"
    assert touched == []


def test_runtime_method_lookup_is_static_even_with_malicious_metaclass():
    from abc import ABCMeta

    a = account()
    d = descriptor()
    looked_up = []

    class MaliciousMeta(ABCMeta):
        def __getattribute__(cls, name):
            if name in {"loans", "accounts"}:
                looked_up.append(name)
                raise AssertionError("no dynamic plugin class attribute lookup")
            return super().__getattribute__(name)

    class MaliciousSession(OnlyList, metaclass=MaliciousMeta):
        pass

    s = MaliciousSession(d, a)
    assert fact("loan.list", d, s, a).availability == "supported"
    assert looked_up == []


def test_bounded_scoped_accounts_fail_closed_without_getter_or_network():
    a = account()
    d = descriptor()
    s = OnlyList(d, a)
    # An oversized catalogue of accounts is not a reason to scan the whole
    # registry or to make a request while simply rendering a button.
    s._accounts = [a] * 513
    result = fact("loan.list", d, s, a)
    assert result.availability == "unavailable"
    assert result.session_ready is False


def test_publication_and_runtime_validators_ignore_dynamic_method_descriptors():
    a = account()
    d = descriptor(loans=True, borrow=True)
    touched = []

    class MethodProperty(OnlyList):
        @property
        def borrow(self):
            touched.append("provider side effect")
            raise AssertionError("no method lookup or POST")

    s = MethodProperty(d, a)
    manifest = ("sync.loans", "acquisition.borrow")
    runtime = fact("loan.borrow", d, s, a, capabilities=manifest)
    assert runtime.reason_code == "method_unimplemented"
    static = _publications(d, MethodProperty, capabilities=manifest)
    assert static["loan.borrow"].publication_status == "conflict"
    assert touched == []


def test_malformed_capability_descriptor_does_not_execute_custom_getters():
    a = account()
    touched = []

    class RemoteCapabilityClaim:
        def __getattr__(self, name):
            touched.append(name)
            raise AssertionError("descriptor property must not run a GET")

    bad_descriptor = replace(
        descriptor(), capabilities=RemoteCapabilityClaim(),
    )
    s = OnlyList(descriptor(), a)
    runtime = assess_lending_operation(
        "loan.list", manifest_capabilities=("sync.loans",),
        descriptor=bad_descriptor, session=s, account=a,
    )
    static = validate_lending_publication(
        manifest_capabilities=("sync.loans",),
        descriptor=bad_descriptor, session_type=OnlyList,
    )
    assert runtime.availability == "unknown" and not runtime.usable
    assert all(item.publication_status == "unverified" for item in static)
    assert touched == []
