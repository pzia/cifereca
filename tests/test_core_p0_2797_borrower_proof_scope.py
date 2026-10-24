"""P0 #2797/#2565: foreign borrower receipts stay outside private audit.

One provider credential may legitimately serve a primary and paired borrower
of the same edition. Only an exact entitlement-id collision or identical
provider/credential/account/edition is relevant foreign conflict evidence.
All local fixtures are synthetic, with no provider or SQLite mutation.
"""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from cifereca.core.library_evidence_scope import scope_evidence
from cifereca.core.library_reconciliation import EvidenceSnapshot


def _right(identity, account, entitlement):
    return {
        "identity_id": identity, "entitlement_id": entitlement,
        "provider_id": "paris", "credential_id": "shared-login",
        "account_id": account, "item_id": "same-edition",
        "entitlement_type": "loan", "state": "active",
    }


def _receipt(identity, account, entitlement, marker):
    return {
        **_right(identity, account, entitlement),
        "acquisition_id": marker, "status": "complete",
        "private_url": "PRIVATE_SIGNED_URL_" + marker,
    }


def test_primary_and_paired_same_login_and_edition_never_share_private_receipts():
    primary = _right("alice", "primary", "primary-right")
    paired = _right("bob", "paired-borrower", "paired-right")
    own = _receipt("alice", "primary", "primary-right", "OWN")
    paired_only = _receipt("bob", "paired-borrower", "paired-right", "PAIRED")
    wrong_identity_same_source = _receipt(
        "bob", "primary", "shadow-right", "CONFLICT_SAME_BORROWER",
    )
    same_right_wrong_source = _receipt(
        "bob", "paired-borrower", "primary-right", "CONFLICT_SAME_ID",
    )
    source = EvidenceSnapshot({
        "entitlements": [primary, paired],
        "acquisition_ledger": [
            own, paired_only, wrong_identity_same_source, same_right_wrong_source,
        ],
    })
    before = deepcopy(source.tables)
    only_alice = scope_evidence(source, ("alice",))
    assert only_alice.tables["entitlements"] == [primary]
    assert [r["acquisition_id"] for r in only_alice.tables["acquisition_ledger"]] == [
        "OWN", "CONFLICT_SAME_BORROWER", "CONFLICT_SAME_ID",
    ]
    # A foreign borrower may have the same ISBN/edition: those receipts never
    # enter this active identity's private graph, even if the login matches.
    private_text = json.dumps(only_alice.tables)
    assert "PRIVATE_SIGNED_URL_PAIRED" not in private_text
    assert "paired-right" not in json.dumps(only_alice.tables["entitlements"])
    only_bob = scope_evidence(source, ("bob",))
    assert only_bob.tables["entitlements"] == [paired]
    assert "PAIRED" in {
        r["acquisition_id"] for r in only_bob.tables["acquisition_ledger"]
    }
    assert source.tables == before


@pytest.mark.parametrize("missing", [None, "", "   ", 15])
def test_unidentified_selected_borrower_never_broadens_foreign_acquisition_scope(missing):
    selected = _right("alice", missing, "right-a")
    foreign = _receipt("bob", missing, "different-right", "PRIVATE_NO_OWNER")
    direct_collision = _receipt("bob", "paired", "right-a", "EXACT_RIGHT_ID")
    scoped = scope_evidence(EvidenceSnapshot({
        "entitlements": [selected],
        "acquisition_ledger": [foreign, direct_collision],
    }), ("alice",))
    assert [r["acquisition_id"] for r in scoped.tables["acquisition_ledger"]] == [
        "EXACT_RIGHT_ID",
    ]


def test_explicit_all_identities_scope_keeps_their_own_sources():
    rights = [_right("alice", "primary", "right-a"),
              _right("bob", "paired", "right-b")]
    receipts = [_receipt("alice", "primary", "right-a", "A"),
                _receipt("bob", "paired", "right-b", "B")]
    scoped = scope_evidence(EvidenceSnapshot({
        "entitlements": rights, "acquisition_ledger": receipts,
    }), ("alice", "bob"))
    assert scoped.tables["entitlements"] == rights
    assert scoped.tables["acquisition_ledger"] == receipts
