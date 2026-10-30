"""#3252: exact selected purchase entrypoint across provider-neutral fields.

Exercises the real Numilog session and signed-card parser with synthetic HTML.
No signed delivery GET, imported ACSM, Core SQLite or implicit consent.
"""
from dataclasses import asdict
import json

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p1_3252_targeted_delivery_offer import rig
from tests.test_p1_3252_rights_observation_snapshot import bound_account
from tests.test_numilog_owned_js_delivery_p0 import SIGNED


def selection(right, **overrides):
    return {
        **dict(edition_id=right.edition_id, right_id=right.right_id,
               kind="purchase", branch="meslivres",
               explicit_user_action=True),
        **overrides,
    }


def test_numilog_exact_download_offer_survives_unknown_right_metadata():
    from dataclasses import replace

    session, broker, reads = rig()
    original = session.rights_observation_snapshot

    def unknown_right(account):
        observed = original(account)
        return replace(observed, rights=tuple(
            replace(row, state="unknown") for row in observed.rights
        ))

    session.rights_observation_snapshot = unknown_right
    try:
        account = bound_account(session)
        first = session.rights_observation_snapshot(account).rights[0]
        assert first.state == "unknown"
        qualified = session.rights_selected_delivery_offer_snapshot(
            account, **selection(first),
        )
        assert qualified.rights[0].state == "unknown"
        assert qualified.rights[0].delivery_attested
        assert SIGNED not in json.dumps(asdict(qualified))
    finally:
        session.close()


def test_selected_numilog_right_qualifies_only_one_owned_card():
    session, broker, reads = rig()
    try:
        account = bound_account(session)
        initial = session.rights_observation_snapshot(account)
        assert len(initial.rights) == 2
        assert all(row.delivery == "unknown" for row in initial.rights)
        chosen = initial.rights[0]
        selected = session.rights_selected_delivery_offer_snapshot(
            account, **selection(chosen),
        )
        assert reads == [session.library_url] * 3
        assert selected.scope == initial.scope
        assert selected.branch == initial.branch == "meslivres"
        assert selected.status == selected.coverage == "partial"
        assert len(selected.rights) == 1
        assert selected.rights[0].delivery == "available"
        assert selected.rights[0].delivery_offer_id.startswith("numilog.card:")
        assert selected.exact(chosen.edition_id, chosen.right_id, "purchase")
        assert not selected.authorizes_mutation
        assert not selected.authorizes_negative_reconciliation
        assert SIGNED not in json.dumps(asdict(selected))
    finally:
        session.close()


@pytest.mark.parametrize("change", [
    {"explicit_user_action": False},
    {"kind": "loan"},
    {"branch": "other"},
    {"edition_id": "different-edition"},
    {"right_id": "other-purchase"},
    {"edition_id": ""},
])
def test_selected_numilog_mismatches_refused_before_private_get(change):
    session, broker, reads = rig()
    try:
        account = bound_account(session)
        initial = session.rights_observation_snapshot(account)
        before = list(reads)
        with pytest.raises(ProviderCapabilityError, match="selected_offer_scope_mismatch"):
            session.rights_selected_delivery_offer_snapshot(
                account, **selection(initial.rights[0], **change),
            )
        assert reads == before
    finally:
        session.close()


def test_selected_numilog_rebind_after_network_does_not_return_old_scope():
    session, broker, reads = rig()
    try:
        account = bound_account(session)
        right = session.rights_observation_snapshot(account).rights[0]
        original = session.rights_delivery_offer_snapshot

        def changed_owner(*args, **kwargs):
            response = original(*args, **kwargs)
            account.identity_id = "identity-B"
            broker.owner = "identity-B"
            return response

        session.rights_delivery_offer_snapshot = changed_owner
        with pytest.raises(ProviderCapabilityError, match="selected_offer_scope_changed"):
            session.rights_selected_delivery_offer_snapshot(
                account, **selection(right),
            )
    finally:
        session.close()
