"""#3722 Reference provider: black-box tests of the currently published ABI.

No network, credentials, ADEPT, canonical SQLite, Core policy or live data.
The Core executor must provide consent, staging, SHA and atomic publication;
these tests deliberately cannot claim those Core obligations.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import zipfile

import pytest

from cifereca.plugin_api import (
    Account, ProviderCapabilityError, ProviderRightObservation, ProviderRightsSnapshot,
)
from cifereca_reference import BRANCH, PLUGIN, ReferenceSession


class Broker:
    def __init__(self, accounts, *, extra=()):
        self.accounts_live = accounts
        self.extra = extra

    def accounts(self, identity_id=None):
        rows = [
            NS(
                scope_version=2, provider_id="reference",
                credential_id=a.credential_id, account_id=a.unique_id,
                identity_id=a.identity_id,
            )
            for a in self.accounts_live
        ]
        rows.extend(self.extra)
        return tuple(r for r in rows
                     if identity_id is None or r.identity_id == identity_id)


def fixture(*, scenario="mixed", mode="two", credential="card-a",
            bulk_count=350, page_size=50):
    session = ReferenceSession(
        credential_id=credential, label="Test", credentials={},
        options={"scenario": scenario, "accounts": mode,
                 "bulk_count": bulk_count, "page_size": page_size},
    )
    accounts = session.connect()
    for index, account in enumerate(accounts):
        account.identity_id = "identity-a" if index == 0 else "identity-b"
    session.identity_broker = Broker(accounts)
    return session, accounts


def qualify(session, account, edition_id, *, kind="purchase"):
    return session.rights_selected_delivery_offer_snapshot(
        account, edition_id=edition_id, right_id="right-" + edition_id,
        kind=kind, branch=BRANCH, explicit_user_action=True,
    )


def candidate(session, account, offer, target, *, edition_id=None, kind="purchase"):
    row = offer.rights[0]
    return session.rights_selected_delivery_candidate(
        account, edition_id=edition_id or row.edition_id,
        right_id=row.right_id, kind=kind, branch=BRANCH,
        delivery_offer_id=row.delivery_offer_id,
        target_dir=target, explicit_user_action=True,
    )


def test_positive_unknown_entitlement_is_selectable_but_inventory_never_downloads():
    session, (a, b) = fixture()
    try:
        inventory = session.rights_observation_snapshot(a)
        assert type(inventory) is ProviderRightsSnapshot
        assert inventory.status == inventory.coverage == "partial"
        assert not inventory.authorizes_negative_reconciliation
        assert not inventory.authorizes_mutation
        assert (inventory.scope.identity_id, inventory.scope.credential_id,
                inventory.scope.account_id) == ("identity-a", "card-a", "reader-a")
        unknown = inventory.exact("epub-002", "right-epub-002", "purchase")
        assert unknown.state == "unknown"
        assert unknown.provider_attested and unknown.delivery == "unknown"
        assert unknown.delivery_offer_id == ""
        assert inventory.exact("loan-003", "right-loan-003", "loan") is not None
        selected = qualify(session, a, "epub-002")
        assert selected.rights[0].state == "unknown"
        assert selected.rights[0].delivery_attested
        assert selected.rights[0].delivery_offer_id.startswith("reference.offer:")
        assert session.diagnostic_state()["staged_attempts"] == 0
        assert inventory.scope != session.rights_observation_snapshot(b).scope
    finally:
        session.close()


@pytest.mark.parametrize(("edition", "state", "delivery", "archived"), [
    ("no-offer-004", "active", "unknown", ""),
    ("refused-005", "active", "unavailable", ""),
    ("archived-006", "active", "unavailable", "archived"),
])
def test_no_positive_offer_even_with_declared_active_right(edition, state, delivery, archived):
    session, (a, _) = fixture()
    try:
        selected = qualify(session, a, edition)
        row = selected.rights[0]
        assert type(row) is ProviderRightObservation
        assert (row.state, row.delivery, row.delivery_unavailable_reason) == (
            state, delivery, archived,
        )
        assert not row.delivery_attested and row.delivery_offer_id == ""
    finally:
        session.close()


def test_no_delivery_without_exact_offer_or_core_selected_action(tmp_path):
    session, (a, b) = fixture()
    try:
        offer = qualify(session, a, "epub-001")
        kwargs = dict(
            edition_id="epub-001", right_id="right-epub-001",
            kind="purchase", branch=BRANCH,
            delivery_offer_id=offer.rights[0].delivery_offer_id,
            target_dir=tmp_path, explicit_user_action=True,
        )
        with pytest.raises(ProviderCapabilityError):
            session.rights_selected_delivery_candidate(b, **kwargs)
        with pytest.raises(ProviderCapabilityError):
            session.rights_selected_delivery_candidate(a, **{**kwargs, "explicit_user_action":False})
        with pytest.raises(ProviderCapabilityError):
            session.rights_selected_delivery_candidate(a, **{**kwargs, "edition_id":"epub-002"})
        with pytest.raises(ProviderCapabilityError):
            session.rights_selected_delivery_candidate(a, **{**kwargs, "delivery_offer_id":"reference.offer:"+"0"*64})
        with pytest.raises(ProviderCapabilityError):
            qualify(session, a, "archived-006")  # no valid selected candidate
            if False else candidate(session, a, qualify(session,a,"archived-006"), tmp_path)
        assert list(tmp_path.iterdir()) == []
        assert session.diagnostic_state()["staged_attempts"] == 0
    finally:
        session.close()


def test_candidate_epub_is_structural_deterministic_and_separate_from_core(tmp_path):
    session, (a, b) = fixture()
    try:
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        one, marker = candidate(session, a, qualify(session, a, "epub-002"), first)
        two, _ = candidate(session, a, qualify(session, a, "epub-002"), second)
        assert one.parent == first and two.parent == second
        assert marker == "reference.synthetic.epub"
        assert one.read_bytes() == two.read_bytes()
        assert hashlib.sha256(one.read_bytes()).digest() == hashlib.sha256(two.read_bytes()).digest()
        assert zipfile.is_zipfile(one)
        with zipfile.ZipFile(one) as archive:
            assert archive.namelist()[0] == "mimetype"
            assert archive.read("mimetype") == b"application/epub+zip"
            assert b"urn:cifereca:reference" in archive.read("OEBPS/content.opf")
        assert session.diagnostic_state()["staged_attempts"] == 2
    finally:
        session.close()


def test_same_sha_across_two_books_and_profiles_does_not_grant_cross_account_offer(tmp_path):
    session, (a, b) = fixture()
    try:
        dirs = [tmp_path / f"out-{i}" for i in range(3)]
        for directory in dirs:
            directory.mkdir()
        a1, _ = candidate(session, a, qualify(session, a, "same-sha-007"), dirs[0])
        a2, _ = candidate(session, a, qualify(session, a, "same-sha-008"), dirs[1])
        b1, _ = candidate(session, b, qualify(session, b, "same-sha-007"), dirs[2])
        assert a1.read_bytes() == a2.read_bytes() == b1.read_bytes()
        offer_a = qualify(session, a, "same-sha-007").rights[0]
        offer_b = qualify(session, b, "same-sha-007").rights[0]
        assert offer_a.delivery_offer_id != offer_b.delivery_offer_id
        with pytest.raises(ProviderCapabilityError):
            session.rights_selected_delivery_candidate(
                b, edition_id="same-sha-007", right_id="right-same-sha-007",
                kind="purchase", branch=BRANCH,
                delivery_offer_id=offer_a.delivery_offer_id,
                target_dir=tmp_path, explicit_user_action=True,
            )
        assert len(list(tmp_path.iterdir())) == 3
    finally:
        session.close()


def test_deterministic_interruption_then_resume_without_partial_file(tmp_path):
    session, (a, _) = fixture()
    try:
        offer = qualify(session, a, "interrupted-009")
        with pytest.raises(ProviderCapabilityError, match="reference_delivery_interrupted"):
            candidate(session, a, offer, tmp_path)
        assert list(tmp_path.iterdir()) == []
        path, _ = candidate(session, a, offer, tmp_path)
        assert zipfile.is_zipfile(path)
        with pytest.raises(ProviderCapabilityError, match="reference_staging_occupied"):
            candidate(session, a, offer, tmp_path)
        assert path.is_file()
    finally:
        session.close()


@pytest.mark.parametrize("change", [
    "caller_identity", "live_account_identity", "owner_removed",
    "broker_replaced", "session_closed", "credential_changed",
])
def test_session_rebind_during_delivery_refuses_before_write(change, tmp_path):
    session, (a, _) = fixture()
    offer = qualify(session, a, "epub-002")
    try:
        def mutate():
            if change == "caller_identity":
                a.identity_id = "identity-c"
            elif change == "live_account_identity":
                session._accounts[0].identity_id = "identity-c"
            elif change == "owner_removed":
                session.identity_broker.accounts_live = []
            elif change == "broker_replaced":
                session.identity_broker = Broker([])
            elif change == "session_closed":
                session.close()
            else:
                a.credential_id = "other-card"
        session._before_write = mutate
        with pytest.raises(ProviderCapabilityError):
            candidate(session, a, offer, tmp_path)
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_qualified_offer_invalidated_on_close_reconnect(tmp_path):
    session, (a, _) = fixture()
    old = qualify(session, a, "epub-001")
    session.close()
    accounts = session.connect()
    accounts[0].identity_id = "identity-a"
    accounts[1].identity_id = "identity-b"
    session.identity_broker = Broker(accounts)
    try:
        with pytest.raises(ProviderCapabilityError):
            candidate(session, accounts[0], old, tmp_path)
        assert not list(tmp_path.iterdir())
    finally:
        session.close()


def test_invalid_staging_symlink_and_preexisting_candidate(tmp_path):
    session, (a, _) = fixture()
    try:
        offer = qualify(session, a, "epub-001")
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real, target_is_directory=True)
        with pytest.raises(ProviderCapabilityError, match="reference_staging_invalid"):
            candidate(session, a, offer, link)
        existing = real / "epub-001.epub"
        existing.write_bytes(b"USER ORIGINAL, NO OVERWRITE")
        with pytest.raises(ProviderCapabilityError, match="reference_staging_occupied"):
            candidate(session, a, offer, real)
        assert existing.read_bytes() == b"USER ORIGINAL, NO OVERWRITE"
    finally:
        session.close()


def test_350_items_paginate_positive_only_no_negative_authority():
    session, (a, _) = fixture(scenario="bulk")
    try:
        cursor = ""
        found = []
        for index in range(7):
            page = session.rights_observation_snapshot(a, resume_cursor=cursor)
            found.extend(x.edition_id for x in page.rights)
            assert len(page.rights) == 50
            assert not page.authorizes_negative_reconciliation
            assert page.status == page.coverage == "partial"
            cursor = page.resume_cursor
            assert (cursor == "") == (index == 6)
        assert len(set(found)) == len(found) == 350
        with pytest.raises(ProviderCapabilityError):
            session.rights_observation_snapshot(a, resume_cursor="offset:12")
        assert session.diagnostic_state()["staged_attempts"] == 0
    finally:
        session.close()


def test_no_inventory_rows_is_still_not_negative_authority():
    session, (a, _) = fixture(scenario="empty")
    try:
        page = session.rights_observation_snapshot(a)
        assert page.rights == ()
        assert page.status == "partial"
        assert not page.authorizes_negative_reconciliation
    finally:
        session.close()


def test_plugin_factory_never_receives_core_storage_and_is_not_default_enabled():
    class Context:
        def broker(self, name):
            assert name == "identities"
            return Broker([])
    plugin = PLUGIN.activate(Context())
    marker = object()
    session = plugin.create_session(
        credential_id="fixture", label="fixture", credentials={}, options={},
        config=marker, storage=marker, diagnostics=marker,
    )
    assert type(session) is ReferenceSession
    assert "storage" not in session.__dict__
    plugin.deactivate()
    manifest = Path(__file__).parents[1] / "cifereca-plugin.json"
    row = json.loads(manifest.read_text("utf-8"))
    assert row["plugins"][0]["enabled_by_default"] is False
    assert row["plugins"][0]["requires_brokers"] == ["identities"]
