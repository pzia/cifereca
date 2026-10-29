"""P1 #3252: actual Kobo personal manifest projected as read-only rights.

All data are synthetic; FakeVault represents the real session's ONE existing
personal-library GET. No acquisition, backup, network, or Core mutation.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import Account, ProviderCapabilityError, ProviderError
from cifereca_kobo_catalog import KoboCatalogService
from tests.test_p1_3252_inflight_personal_manifest import FakeVault, _session


class OwnerBroker:
    def __init__(self, identities=("identity-A",)):
        self.identities = identities
        self.calls = 0

    def accounts(self):
        self.calls += 1
        return tuple(
            NS(provider_id="kobo", account_id="alice", identity_id=identity)
            for identity in self.identities
        )


def _rig(rows, identities=("identity-A",)):
    vault = FakeVault(rows)
    session = _session(vault)
    session.catalog = KoboCatalogService(
        vault, credential_id="synthetic-card", provider_id="kobo",
    )
    broker = OwnerBroker(identities)
    session.identity_broker = broker
    account = Account(
        display_name="Synthetic", unique_id="alice",
        provider_id="kobo", credential_id="synthetic-card",
        identity_id="identity-A",
    )
    return session, vault, broker, account


def test_real_manifest_bridge_scopes_zero_one_many_and_archived_without_delivery():
    rows = [
        {"product_id": "book-1", "account_id": "alice",
         "owned": True, "downloadable": True, "title": "Book one"},
        {"product_id": "book-2", "account_id": "alice",
         "owned": True, "downloadable": False, "archived": True,
         "collection": "archived", "title": "Book two"},
        {"product_id": "catalogue-only", "account_id": "alice",
         "owned": False, "title": "Not a purchase"},
    ]
    session, vault, broker, account = _rig(rows)
    try:
        report = session.rights_observation_snapshot(account)
        assert vault.calls == 1
        assert broker.calls == 3
        assert report.scope.identity_id == "identity-A"
        assert report.scope.provider_id == "kobo"
        assert report.scope.credential_id == "synthetic-card"
        assert report.scope.account_id == "alice"
        assert report.status == report.coverage == "partial"
        assert report.claims_complete_branch is False
        assert report.pagination_complete is False
        assert not report.authorizes_negative_reconciliation
        assert not report.authorizes_mutation
        assert report.branch == "personal_manifest"
        assert [(r.edition_id, r.state) for r in report.rights] == [
            ("book-1", "active"), ("book-2", "active"),
        ]
        assert all(r.kind == "purchase" for r in report.rights)
        assert all(r.provider_attested for r in report.rights)
        assert all(r.evidence_ref == "kobo.personal_manifest" for r in report.rights)
        assert [r.delivery for r in report.rights] == ["unknown", "unavailable"]
        assert all(not r.delivery_offer_id for r in report.rights)
        assert all(not r.authorizes_mutation for r in report.rights)
        assert report.exact("book-1", "book-1", "purchase") is report.rights[0]
        assert report.exact("book-1", "book-1", "loan") is None
        assert report.exact("book-3", "book-3", "purchase") is None
        assert session.backup_manifest_policy["manifest_authoritative"] is False
    finally:
        session.close()

    empty, vault, broker, account = _rig([])
    try:
        report = empty.rights_observation_snapshot(account)
        assert report.rights == ()
        assert report.status == "partial" and report.coverage == "partial"
        assert vault.calls == 1
        assert not report.authorizes_negative_reconciliation
    finally:
        empty.close()


@pytest.mark.parametrize("identities", [
    (), ("identity-B",), ("identity-A", "identity-B"),
    ("identity-A", "identity-A"),
])
def test_missing_foreign_or_ambiguous_core_owner_refuses_before_vault(identities):
    session, vault, broker, account = _rig(
        [{"product_id": "book-1", "owned": True}], identities,
    )
    try:
        with pytest.raises(ProviderCapabilityError, match="propriétaire unique"):
            session.rights_observation_snapshot(account)
        assert vault.calls == 0
    finally:
        session.close()


@pytest.mark.parametrize("change", [
    {"identity_id": "identity-B"}, {"provider_id": "numilog"},
    {"unique_id": "other-account"}, {"credential_id": "vault-B"},
])
def test_untrusted_account_object_cannot_rebind_kobo_rights(change):
    session, vault, broker, account = _rig(
        [{"product_id": "book-1", "owned": True}],
    )
    try:
        with pytest.raises(ProviderCapabilityError):
            session.rights_observation_snapshot(replace(account, **change))
        assert vault.calls == 0
    finally:
        session.close()


def test_without_identity_broker_the_legacy_library_still_works_but_new_api_refuses():
    session, vault, broker, account = _rig([
        {"product_id": "book-1", "account_id": "alice", "owned": True},
    ])
    session.identity_broker = None
    try:
        with pytest.raises(ProviderCapabilityError, match="propriétaire unique"):
            session.rights_observation_snapshot(account)
        assert vault.calls == 0
        assert [r.record_id for r in session.library()] == ["book-1"]
        assert vault.calls == 1
    finally:
        session.close()


@pytest.mark.parametrize("race", ["owner_changed", "owner_ambiguous", "broker_changed",
                                  "session_reconnected"])
def test_inflight_owner_or_connection_change_blocks_positive_observation(race):
    session, vault, broker, account = _rig([
        {"product_id": "book-1", "account_id": "alice", "owned": True},
    ])
    original = vault.library_manifest

    def during_read():
        rows = original()
        if race == "owner_changed":
            broker.identities = ("identity-B",)
        elif race == "owner_ambiguous":
            broker.identities = ("identity-A", "identity-B")
        elif race == "broker_changed":
            session.identity_broker = OwnerBroker()
        else:
            session._personal_binding_generation += 1
        return rows

    vault.library_manifest = during_read
    try:
        with pytest.raises((ProviderCapabilityError, ProviderError)):
            session.rights_observation_snapshot(account)
        assert vault.calls == 1
    finally:
        session.close()


def test_duplicate_foreign_and_malformed_products_never_become_positive_rights():
    session, vault, broker, account = _rig([
        {"product_id": "shared", "account_id": "alice", "owned": True},
        {"product_id": "shared", "account_id": "alice", "owned": True},
        {"product_id": "foreign", "account_id": "other", "owned": True},
        {"product_id": "safe", "account_id": "alice", "owned": True},
        {"product_id": "notsure", "account_id": "alice", "owned": "true"},
    ])
    try:
        report = session.rights_observation_snapshot(account)
        assert [r.edition_id for r in report.rights] == ["safe"]
        assert session.last_library_rejections
        assert report.status == "partial" and report.pagination_complete is False
        assert vault.calls == 1
        assert report.exact("shared", "shared", "purchase") is None
        assert report.exact("foreign", "foreign", "purchase") is None
    finally:
        session.close()


def test_inconsistent_archived_marker_cannot_publish_false_active_purchase():
    session, vault, broker, account = _rig([
        {"product_id": "bad", "account_id": "alice", "owned": True,
         "archived": "false"},
        {"product_id": "good", "account_id": "alice", "owned": True,
         "archived": False},
    ])
    try:
        report = session.rights_observation_snapshot(account)
        assert [r.edition_id for r in report.rights] == ["good"]
        assert report.rights[0].state == "active"
    finally:
        session.close()


@pytest.mark.parametrize(("field", "replacement"), [
    ("identity_id", "identity-B"),
    ("provider_id", "numilog"),
    ("credential_id", "other-credential"),
    ("unique_id", "bob"),
])
def test_inflight_caller_account_rebind_never_publishes_kobo_rights(
    field, replacement,
):
    """Owner broker and Vault session stay stable while caller Account changes."""
    session, vault, broker, account = _rig([
        {"product_id": "book-1", "account_id": "alice", "owned": True},
    ])
    original = vault.library_manifest

    def rebind_during_read():
        result = original()
        setattr(account, field, replacement)
        return result

    vault.library_manifest = rebind_during_read
    try:
        with pytest.raises(ProviderCapabilityError, match="propriétaire modifié"):
            session.rights_observation_snapshot(account)
        assert vault.calls == 1
        assert broker.identities == ("identity-A",)
    finally:
        session.close()


def test_kobo_caller_rebind_during_projection_is_rejected():
    session, vault, broker, account = _rig([
        {"product_id": "book-1", "account_id": "alice", "owned": True},
    ])
    original = session.normalize_item

    def rebind_in_normalization(*args, **kwargs):
        result = original(*args, **kwargs)
        account.identity_id = "identity-B"
        return result

    session.normalize_item = rebind_in_normalization
    try:
        with pytest.raises(ProviderCapabilityError, match="propriétaire modifié"):
            session.rights_observation_snapshot(account)
        assert vault.calls == 1
    finally:
        session.close()


@pytest.mark.parametrize("archived,collection", [
    (True, "library"),
    (True, "archived"),
    (False, "archived"),
])
def test_archived_kobo_book_is_still_an_active_purchase_not_an_expired_right(
    archived, collection,
):
    """Archived is not revoked: preserve purchase separately from delivery."""
    session, vault, _broker, account = _rig([{
        "product_id": "edition-archived", "account_id": "alice",
        "title": "Synthetic archived purchase", "owned": True,
        "downloadable": False, "archived": archived,
        "collection": collection,
    }])
    try:
        observed = session.rights_observation_snapshot(account)
        assert len(observed.rights) == 1
        right = observed.rights[0]
        assert right.kind == "purchase"
        assert right.state == "active"
        assert right.delivery == ("unavailable" if archived is True else "unknown")
        assert right.delivery_offer_id == ""
        assert not observed.authorizes_mutation
        assert not observed.authorizes_negative_reconciliation
        assert vault.calls == 1
        assert session.backup_manifest_policy["acquisition_authorized"] is False
    finally:
        session.close()


def test_explicitly_not_owned_archived_book_does_not_produce_purchase_right():
    session, vault, _broker, account = _rig([{
        "product_id": "edition-revoked", "account_id": "alice",
        "owned": False, "archived": True, "collection": "archived",
    }])
    try:
        observed = session.rights_observation_snapshot(account)
        assert observed.rights == ()
        assert observed.coverage == observed.status == "partial"
        assert not observed.authorizes_negative_reconciliation
        assert vault.calls == 1
    finally:
        session.close()


@pytest.mark.parametrize("other_owner", ["identity-A", "identity-B"])
def test_kobo_v2_two_credentials_one_upstream_account_are_independent(other_owner):
    s, vault, broker, account = _rig([
        {"product_id": "book-1", "account_id": "alice", "owned": True},
    ])
    broker.accounts = lambda: [
        NS(provider_id="kobo", account_id="alice", identity_id="identity-A",
           credential_id="synthetic-card", scope_version=2),
        NS(provider_id="kobo", account_id="alice", identity_id=other_owner,
           credential_id="other-credential", scope_version=2),
    ]
    try:
        rights = s.rights_observation_snapshot(account)
        assert [(x.right_id, x.state) for x in rights.rights] == [
            ("book-1", "active"),
        ]
        assert rights.scope.credential_id == "synthetic-card"
        assert vault.calls == 1
        assert not rights.authorizes_negative_reconciliation
    finally:
        s.close()


@pytest.mark.parametrize("refs", [
    [NS(provider_id="kobo", account_id="alice", identity_id="identity-A",
        credential_id="other-credential", scope_version=2)],
    [NS(provider_id="kobo", account_id="alice", identity_id="identity-A",
        credential_id="", scope_version=2)],
    [NS(provider_id="kobo", account_id="alice", identity_id="identity-A",
        credential_id="synthetic-card", scope_version=2),
     NS(provider_id="kobo", account_id="alice", identity_id="identity-A")],
])
def test_kobo_v2_wrong_credential_and_ambiguous_mixed_v1_v2_before_vault(refs):
    s, vault, broker, account = _rig([
        {"product_id": "book-1", "account_id": "alice", "owned": True},
    ])
    broker.accounts = lambda: refs
    try:
        with pytest.raises(ProviderCapabilityError, match="propriétaire unique"):
            s.rights_observation_snapshot(account)
        assert vault.calls == 0
    finally:
        s.close()


def test_kobo_v2_credential_rebound_during_manifest_read_does_not_emit_rights():
    s, vault, broker, account = _rig([
        {"product_id": "book-1", "account_id": "alice", "owned": True},
    ])
    broker_credential = ["synthetic-card"]

    def refs():
        return [NS(provider_id="kobo", account_id="alice",
                   identity_id="identity-A",
                   credential_id=broker_credential[0], scope_version=2)]

    broker.accounts = refs
    original = vault.library_manifest

    def rebound():
        rows = original()
        broker_credential[0] = "other-credential"
        return rows

    vault.library_manifest = rebound
    try:
        with pytest.raises(ProviderCapabilityError, match="propriétaire modifié"):
            s.rights_observation_snapshot(account)
        assert vault.calls == 1
    finally:
        s.close()
