"""Offline reconciliation for Paris loan visibility and ADEPT account grants."""
from __future__ import annotations

import json
from types import SimpleNamespace

from cifereca import cli
from cifereca.paris_adept_audit import build_paris_adept_audit
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


class FakeStorage:
    def __init__(self, *, entitlements=None, acquisitions=None, grants=None):
        self.rights = entitlements or []
        self.ledger = acquisitions or []
        self.grants = grants or []
        self.calls = []

    def entitlements(self, *, provider_id=None, state=None):
        self.calls.append("entitlements")
        assert (provider_id, state) == ("paris", "active")
        return self.rights

    def acquisitions(self, *, provider_id=None):
        self.calls.append("acquisitions")
        assert provider_id == "paris"
        return self.ledger

    def paris_borrower_adept_bindings(self):
        self.calls.append("bindings")
        return self.grants

    def provider_connections(self):
        self.calls.append("connections")
        return [
            {"provider_id": "paris", "credential_id": "paris-a", "identity_id": "alice"},
            {"provider_id": "paris", "credential_id": "paris-b", "identity_id": "bob"},
        ]

    def adept_profile_binding_get(self, scope, identity):
        assert scope == "identity"
        return {"alice": "profile-a", "bob": "profile-b"}.get(identity)


def right(credential, identity, borrower, loan="shared", item="book"):
    return {
        "provider_id": "paris", "credential_id": credential,
        "account_id": borrower, "identity_id": identity, "item_id": item,
        "loan_id": loan,
    }


def grant(credential, owner, profile):
    return {
        "credential_id": credential, "account_id": "linked",
        "connection_identity_id": "alice" if credential == "paris-a" else "bob",
        "borrower_identity_id": owner, "profile_id": profile,
        "confirmed_by": "explicit",
    }


def test_shared_loan_with_two_confirmed_profiles_is_flagged():
    storage = FakeStorage(
        entitlements=[
            right("paris-a", "alice", "linked"),
            right("paris-b", "bob", "linked"),
        ],
        grants=[grant("paris-a", "alice", "profile-a"), grant("paris-b", "bob", "profile-b")],
    )
    report = build_paris_adept_audit(storage)
    assert report["summary"]["multiple_connection_groups"] == 1
    assert report["summary"]["profile_disagreements"] == 1
    assert report["summary"]["attention_groups"] == 1
    shared = report["loans"][0]
    assert shared["multiple_connections"] is True
    assert shared["profile_disagreement"] is True
    assert {r["binding_state"] for r in shared["exposures"]} == {"locally_consistent"}
    assert set(storage.calls) == {
        "connections", "bindings", "entitlements", "acquisitions"
    } | set()


def test_filter_does_not_hide_other_connection_for_same_loan():
    storage = FakeStorage(entitlements=[
        right("paris-a", "alice", "primary", loan="shared"),
        right("paris-b", "bob", "primary", loan="shared"),
        right("paris-b", "bob", "primary", loan="unrelated"),
    ])
    report = build_paris_adept_audit(storage, credential_id="paris-a")
    assert [x["loan_id"] for x in report["loans"]] == ["shared"]
    assert len(report["loans"][0]["exposures"]) == 2


def test_missing_or_revoked_linked_borrower_binding_never_looks_proven():
    storage = FakeStorage(entitlements=[right("paris-a", "alice", "linked")])
    report = build_paris_adept_audit(storage)
    loan = report["loans"][0]
    assert loan["requires_attention"] is True
    assert loan["exposures"][0]["binding_state"] == "binding_required"
    assert loan["exposures"][0]["adept_profile_id"] is None


def test_ledger_origin_profile_conflict_is_visible_without_entitlement():
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[{
            "credential_id": "paris-a", "account_id": "primary",
            "identity_id": "alice", "item_id": "book", "status": "complete",
            "metadata": {
                "provider_loan_id": "shared",
                "adept_binding": {"adept_profile_id": "unknown-previous-profile"},
            },
        }],
    )
    report = build_paris_adept_audit(storage)
    row = report["loans"][0]
    assert row["profile_disagreement"] is True
    assert row["exposures"][0]["recorded_adept_profile_id"] == "unknown-previous-profile"
    assert row["exposures"][0]["sources"] == ["acquisition", "entitlement"]


def test_different_items_on_same_loan_id_are_visible_not_treated_as_proven_equivalence():
    storage = FakeStorage(entitlements=[
        right("paris-a", "alice", "primary", item="isbn-one"),
        right("paris-b", "bob", "primary", item="isbn-two"),
    ])
    report = build_paris_adept_audit(storage)
    assert report["loans"][0]["multiple_item_ids"] is True
    assert report["loans"][0]["requires_attention"] is True
    assert "ne prouvent pas" in report["note"]


def test_cli_audit_is_read_only_and_never_contacts_paris(tmp_path, monkeypatch, capsys):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service._persist_active_loans("alice", [loan])
        core = SimpleNamespace(library=service, storage=service.storage, close=lambda: None)
        monkeypatch.setattr(cli.CiferecaCore, "open", lambda **kwargs: core)
        monkeypatch.setattr(
            service, "connect_configured",
            lambda: (_ for _ in ()).throw(AssertionError("offline audit must not connect")),
        )
        result = cli.main(["paris-adept", "audit", "--credential", "alice-paris"])
        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"]["loan_groups"] == 1
        assert payload["loans"][0]["exposures"][0]["binding_state"] == "binding_required"
        assert service.storage.acquisitions(provider_id="paris") == []
    finally:
        service.close()
