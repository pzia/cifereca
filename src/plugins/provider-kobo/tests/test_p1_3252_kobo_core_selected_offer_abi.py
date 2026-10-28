"""#3252: selected-right API contract, synthetic Vault only."""
import pytest
from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p1_3252_kobo_selected_delivery_offer import setup

def select(**changes):
    value = dict(edition_id="book-1", right_id="book-1",
                 kind="purchase", branch="personal_manifest",
                 explicit_user_action=True)
    value.update(changes)
    return value

def test_selected_kobo_one_right_one_preflight():
    session, vault, broker, account, posts = setup()
    try:
        result = session.rights_selected_delivery_offer_snapshot(
            account, **select(),
        )
        assert len(result.rights) == len(posts) == 1
        assert result.rights[0].delivery == "available"
        assert result.status == result.coverage == "partial"
        assert not result.authorizes_mutation
    finally:
        session.close()

@pytest.mark.parametrize("changes", [
    {"explicit_user_action": False},
    {"kind": "loan"},
    {"branch": "other"},
    {"edition_id": "different"},
])
def test_selected_kobo_wrong_key_has_no_network(changes):
    session, vault, broker, account, posts = setup()
    try:
        with pytest.raises(ProviderCapabilityError):
            session.rights_selected_delivery_offer_snapshot(
                account, **select(**changes),
            )
        assert vault.calls == 0 and not posts
    finally:
        session.close()

def test_kobo_missing_selected_purchase_cannot_start_post():
    session, vault, broker, account, posts = setup()
    try:
        with pytest.raises(ProviderCapabilityError, match="right_unobserved"):
            session.rights_selected_delivery_offer_snapshot(
                account, **select(edition_id="unowned", right_id="unowned"),
            )
        assert vault.calls == 1 and not posts
    finally:
        session.close()

@pytest.mark.parametrize("change", ["identity", "broker", "account"])
def test_kobo_rebind_after_offer_projection_discarded(change):
    from dataclasses import replace
    from tests.test_p1_3252_rights_observation_snapshot import OwnerBroker

    session, vault, broker, account, posts = setup()
    original = session.rights_delivery_offer_snapshot
    def during_projection(*args, **kwargs):
        offer = original(*args, **kwargs)
        if change == "identity":
            broker.identities = ("identity-B",)
        elif change == "broker":
            session.identity_broker = OwnerBroker()
        else:
            session._accounts = [replace(session._accounts[0])]
        return offer
    session.rights_delivery_offer_snapshot = during_projection
    try:
        with pytest.raises(ProviderCapabilityError, match="owner_changed"):
            session.rights_selected_delivery_offer_snapshot(
                account, **select(),
            )
        assert len(posts) == 1
    finally:
        session.close()
