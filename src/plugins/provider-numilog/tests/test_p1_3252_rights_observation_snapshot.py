"""#3252: Numilog versioned rights snapshot from one authenticated MesLivres read.

No actual HTTP, provider acquisition, ACSM, vault, storage or rights mutation.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import Account, ProviderCapabilityError
from cifereca_numilog import NumilogSession


class IdentityBroker:
    def __init__(self):
        self.owner = "identity-A"
        self.calls = 0
        self.account_id = ""

    def accounts(self):
        self.calls += 1
        if self.owner is None:
            return []
        return [SimpleNamespace(
            provider_id="numilog", account_id=self.account_id,
            identity_id=self.owner,
        )]


def session(*, with_broker=True):
    broker = IdentityBroker()
    obj = NumilogSession(
        credential_id="synthetic-account", label="Numilog",
        credentials={}, options={}, config={}, diagnostics=None,
        identity_broker=broker if with_broker else None,
    )
    obj.connected = True
    broker.account_id = "numilog:" + obj.scope
    # A normal connected NumilogSession publishes exactly this provider
    # Account. Older library-only tests did not need to populate it.
    obj._accounts = [Account(
        display_name="Test provider account", unique_id=broker.account_id,
        provider_id="numilog", credential_id=obj.credential_id,
    )]
    return obj, broker


def owned_row():
    return {
        "lid": "42", "variant": "()", "title": "Livre synthétique",
        "cover": None, "public_url": None,
    }


def _candidate_broker(session_obj, identities):
    broker = SimpleNamespace(identities=list(identities), calls=0)
    def accounts():
        broker.calls += 1
        return [
            SimpleNamespace(provider_id="numilog",
                            account_id="numilog:" + session_obj.scope,
                            identity_id=owner)
            for owner in broker.identities
        ]
    broker.accounts = accounts
    return broker


def bound_account(session_obj, identity="identity-A"):
    return Account(
        display_name="Test borrower", unique_id="numilog:" + session_obj.scope,
        provider_id="numilog", credential_id=session_obj.credential_id,
        identity_id=identity,
    )


def test_numilog_attests_positive_rows_but_no_delivery_or_negative_right():
    s, broker = session()
    reads = []
    s._rows = lambda **kw: (reads.append(dict(kw)) or [
        owned_row(),
        {**owned_row(), "lid": "43", "variant": "(pdf)"},
    ])
    try:
        report = s.rights_observation_snapshot(bound_account(s))
        assert len(reads) == 1 and reads[0] == {"rights_observation": True}
        assert broker.calls == 3  # before read, after read and before publish
        assert report.scope.identity_id == "identity-A"
        assert report.scope.account_id == "numilog:" + s.scope
        assert report.scope.credential_id == s.credential_id
        assert report.status == report.coverage == "partial"
        assert report.claims_complete_branch is False
        assert report.pagination_complete is False
        assert report.authorizes_mutation is False
        assert report.authorizes_negative_reconciliation is False
        assert len(report.rights) == 2
        first = report.rights[0]
        assert first.kind == "purchase"
        assert first.state == "active"
        assert first.provider_attested
        assert first.evidence_ref == "numilog.meslivres"
        assert first.delivery == "unknown"
        assert first.delivery_offer_id == ""
        assert first.authorizes_mutation is False
        assert report.exact(first.edition_id, first.right_id, "purchase") == first
        assert report.exact(first.edition_id, first.right_id, "loan") is None
        with pytest.raises(FrozenInstanceError):
            report.scope.identity_id = "identity-B"
    finally:
        s.close()


@pytest.mark.parametrize("owner", (None, "identity-B"))
def test_wrong_or_missing_owner_blocks_before_authenticated_inventory_read(owner):
    s, broker = session()
    broker.owner = owner
    calls = []
    s._rows = lambda **kw: (calls.append("GET") or [owned_row()])
    try:
        with pytest.raises(ProviderCapabilityError, match="propriétaire unique"):
            s.rights_observation_snapshot(bound_account(s))
        assert calls == []
    finally:
        s.close()


@pytest.mark.parametrize("owners", (
    [], ["identity-A", "identity-B"], ["identity-A", "identity-A"],
))
def test_ambiguous_core_identity_binding_fails_closed_without_network(owners):
    s, _ = session()
    broker = _candidate_broker(s, owners)
    s.identity_broker = broker
    seen = []
    s._rows = lambda **kw: (seen.append("GET") or [owned_row()])
    try:
        with pytest.raises(ProviderCapabilityError, match="propriétaire unique"):
            s.rights_observation_snapshot(bound_account(s))
        assert seen == []
    finally:
        s.close()


@pytest.mark.parametrize("misbind", (
    "identity-B", "missing-account", "other-provider", "other-credential",
))
def test_fake_account_cannot_rebind_numilog_rights(misbind):
    s, broker = session()
    acc = bound_account(s)
    from dataclasses import replace
    kwargs = {
        "identity-B": {"identity_id": "identity-B"},
        "missing-account": {"unique_id": "numilog:another-account"},
        "other-provider": {"provider_id": "kobo"},
        "other-credential": {"credential_id": "vault-B"},
    }[misbind]
    acc = replace(acc, **kwargs)
    calls = []
    s._rows = lambda **kw: (calls.append("GET") or [owned_row()])
    try:
        with pytest.raises(ProviderCapabilityError):
            s.rights_observation_snapshot(acc)
        assert calls == []
    finally:
        s.close()


@pytest.mark.parametrize("change", ("revoked", "other-owner", "broker-replaced"))
def test_inflight_identity_change_blocks_positive_purchase_observations(change):
    s, broker = session()
    before = broker
    calls = []
    def rows(**kw):
        calls.append("GET")
        if change == "revoked":
            broker.owner = None
        elif change == "other-owner":
            broker.owner = "identity-B"
        else:
            s.identity_broker = _candidate_broker(s, ["identity-A"])
        return [owned_row()]
    s._rows = rows
    try:
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
            s.rights_observation_snapshot(bound_account(s))
        assert calls == ["GET"]
    finally:
        s.close()


def test_brokerless_legacy_library_cannot_publish_scoped_rights():
    s, _ = session(with_broker=False)
    calls = []
    s._rows = lambda **kw: (calls.append("GET") or [owned_row()])
    try:
        with pytest.raises(ProviderCapabilityError, match="propriétaire unique"):
            s.rights_observation_snapshot(bound_account(s))
        assert calls == []
        assert len(s.library()) == 1  # old public behavior preserved
    finally:
        s.close()


def test_invalid_upstream_row_does_not_become_a_right():
    s, _ = session()
    s._rows = lambda **kw: [
        owned_row(),
        {**owned_row(), "lid": "43", "variant": None},
    ]
    try:
        result = s.rights_observation_snapshot(bound_account(s))
        assert len(result.rights) == 1
        assert result.claims_complete_branch is False
        assert not result.authorizes_negative_reconciliation
    finally:
        s.close()


def test_repeated_same_edition_never_creates_two_attested_purchase_rows():
    s, _ = session()
    s._rows = lambda **kw: [owned_row(), owned_row()]
    try:
        with pytest.raises(ValueError, match="ambiguous_rights_observation_rows"):
            s.rights_observation_snapshot(bound_account(s))
    finally:
        s.close()


@pytest.mark.parametrize(("field", "replacement"), [
    ("identity_id", "identity-B"),
    ("provider_id", "kobo"),
    ("credential_id", "other-credential"),
    ("unique_id", "numilog:another-account"),
])
def test_inflight_caller_account_rebind_never_publishes_numilog_rights(
    field, replacement,
):
    """The mutable caller Account is independent of the Core owner broker."""
    s, broker = session()
    account = bound_account(s)
    reads = []

    def rows(**kwargs):
        reads.append(kwargs)
        setattr(account, field, replacement)
        return [owned_row()]

    s._rows = rows
    try:
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
            s.rights_observation_snapshot(account)
        assert reads == [{"rights_observation": True}]
        assert broker.owner == "identity-A"  # broker alone would miss this
    finally:
        s.close()


def test_numilog_caller_rebind_during_projection_is_rejected():
    s, broker = session()
    account = bound_account(s)
    s._rows = lambda **kwargs: [owned_row()]
    original_id = s._id

    def rebind_in_normalization(row):
        account.identity_id = "identity-B"
        return original_id(row)

    s._id = rebind_in_normalization
    try:
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
            s.rights_observation_snapshot(account)
        assert broker.owner == "identity-A"
    finally:
        s.close()


def test_numilog_duplicate_purchase_cards_quarantined_in_typed_observation_only(
    monkeypatch,
):
    """One repeated edition is ambiguous; other purchases remain positive.

    Legacy library() still deduplicates for display. The typed rights read
    removes the prior occurrence and the duplicate, on every source page.
    """
    import cifereca_numilog as provider

    s, broker = session()
    page2_url = s.library_url + "?page=2"
    reads = []
    page1 = [
        owned_row(),
        {**owned_row(), "lid": "43", "title": "Safe purchase 43"},
    ]
    page2 = [
        {**owned_row(), "lid": "42", "title": "Conflicting duplicate"},
        {**owned_row(), "lid": "44", "title": "Safe purchase 44"},
    ]

    def page(url, **kwargs):
        reads.append(url)
        return (url, "PAGE2" if "page=2" in url else "PAGE1")

    def parse_page(html, url, **kwargs):
        if html == "PAGE1":
            return list(page1), page2_url
        assert html == "PAGE2"
        return list(page2), None

    s._page = page
    monkeypatch.setattr(provider, "parse_library_page", parse_page)
    try:
        normal = s.library()
        assert len(normal) == 3
        assert [row.title for row in normal] == [
            "Livre synthétique", "Safe purchase 43", "Safe purchase 44",
        ]
        evidence = s.rights_observation_snapshot(bound_account(s))
        assert len(evidence.rights) == 2
        assert evidence.rights[0].edition_id == s._id(page1[1])
        assert evidence.rights[1].edition_id == s._id(page2[1])
        assert evidence.exact(s._id(page1[0]), s._id(page1[0]), "purchase") is None
        assert evidence.status == evidence.coverage == "partial"
        assert not evidence.authorizes_negative_reconciliation
        assert reads == [s.library_url, page2_url, s.library_url, page2_url]
        assert broker.calls >= 3
    finally:
        s.close()


def test_numilog_strict_rights_rows_never_load_delivery_on_duplicate(monkeypatch):
    """Even a tainted card triggers no scoped ACSM URL parsing or import."""
    import cifereca_numilog as provider

    s, _broker = session()
    hints = []
    rows = [owned_row(), owned_row()]
    s._page = lambda url, **kwargs: (url, "SYNTHETIC")
    def parse_page(_html, _url, **kwargs):
        hints.append(kwargs)
        return list(rows), None
    monkeypatch.setattr(provider, "parse_library_page", parse_page)
    try:
        evidence = s.rights_observation_snapshot(bound_account(s))
        assert evidence.rights == ()
        assert evidence.status == "partial"
        assert len(hints) == 1
        assert hints[0]["delivery_evidence"] is False
        assert hints[0]["delivery_selector"] is None
    finally:
        s.close()


@pytest.mark.parametrize("change", [
    "replace_same_scope", "replace_other_identity",
    "duplicate_same_scope", "remove",
])
def test_numilog_rights_refuse_live_account_replacement_during_inventory(change):
    """A stale session Account cannot authorize purchases after reconnect A->A."""
    from dataclasses import replace

    s, broker = session()
    bound = s._accounts[0]
    active = bound
    seen = []

    def rows(**kwargs):
        seen.append(kwargs)
        if change == "remove":
            s._accounts = []
        elif change == "duplicate_same_scope":
            s._accounts.append(replace(bound))
        else:
            other = replace(bound)
            if change == "replace_other_identity":
                other.identity_id = "identity-B"
            s._accounts = [other]
        return [owned_row()]

    s._rows = rows
    try:
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
            s.rights_observation_snapshot(bound_account(s))
        assert seen == [{"rights_observation": True}]
        assert broker.owner == "identity-A"
    finally:
        s.close()


def test_numilog_rights_refuse_account_replacement_after_field_projection():
    """The final guard must re-check the actual active Account, not only Core."""
    from dataclasses import replace

    s, broker = session()
    s._rows = lambda **kwargs: [owned_row()]
    original = s._id

    def rebind_after_normalization(row):
        result = original(row)
        s._accounts = [replace(s._accounts[0])]
        return result

    s._id = rebind_after_normalization
    try:
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
            s.rights_observation_snapshot(bound_account(s))
        assert broker.owner == "identity-A"
    finally:
        s.close()
