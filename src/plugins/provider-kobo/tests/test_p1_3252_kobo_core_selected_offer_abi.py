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

def test_kobo_unknown_right_state_does_not_veto_exact_downloadable_file():
    session, vault, broker, account, posts = setup()
    # Authenticated personal manifest, exact downloadable product, but no
    # owned claim. Its descriptor is still an independently observed offer.
    vault.rows[0].pop("owned")
    try:
        inventory = session.rights_observation_snapshot(account)
        observed = inventory.exact("book-1", "book-1", "purchase")
        assert observed is not None and observed.state == "unknown"
        assert observed.delivery == "unknown"
        selected = session.rights_selected_delivery_offer_snapshot(
            account, **select(),
        )
        assert selected.rights[0].state == "unknown"
        assert selected.rights[0].delivery_attested
        assert len(posts) == 1  # explicit descriptor POST, not file GET
        assert vault.calls == 2  # one inventory read and one selected read
    finally:
        session.close()


def test_kobo_personal_downloadable_file_with_owned_false_can_offer_delivery():
    session, vault, broker, account, posts = setup()
    # Ownership metadata is not a delivery capability. The private per-account
    # manifest reports a downloadable file and Vault attests its exact HTTPS
    # descriptor only after Core-selected preflight.
    vault.rows[0]["owned"] = False
    try:
        rights = session.rights_observation_snapshot(account)
        row = rights.exact("book-1", "book-1", "purchase")
        assert row is not None and row.state == "unknown"
        assert row.delivery == "unknown" and len(posts) == 0
        selected = session.rights_selected_delivery_offer_snapshot(
            account, **select(),
        )
        assert selected.rights[0].state == "unknown"
        assert selected.rights[0].delivery_attested
        assert len(posts) == 1
    finally:
        session.close()


def test_kobo_explicit_not_owned_without_downloadable_flag_is_not_a_file():
    session, vault, broker, account, posts = setup()
    vault.rows[0]["owned"] = False
    vault.rows[0]["downloadable"] = None
    try:
        inventory = session.rights_observation_snapshot(account)
        assert inventory.exact("book-1", "book-1", "purchase") is None
        with pytest.raises(ProviderCapabilityError, match="right_unobserved"):
            session.rights_selected_delivery_offer_snapshot(account, **select())
        assert posts == []
    finally:
        session.close()


@pytest.mark.parametrize("field,bad", [
    ("owned", "false"), ("owned", 0), ("downloadable", "true"),
    ("downloadable", 1),
])
def test_kobo_malformed_manifest_permission_flags_never_become_file_capability(
    field, bad,
):
    session, vault, broker, account, posts = setup()
    vault.rows[0][field] = bad
    try:
        inventory = session.rights_observation_snapshot(account)
        assert inventory.exact("book-1", "book-1", "purchase") is None
        with pytest.raises(ProviderCapabilityError, match="right_unobserved"):
            session.rights_selected_delivery_offer_snapshot(
                account, **select(),
            )
        assert posts == []
    finally:
        session.close()


def test_kobo_downloadable_owned_false_archive_remains_skipped_without_post():
    session, vault, broker, account, posts = setup()
    vault.rows[0].update(owned=False, downloadable=True, archived=True)
    try:
        inventory = session.rights_observation_snapshot(account)
        right = inventory.exact("book-1", "book-1", "purchase")
        assert right is not None and right.state == "unknown"
        assert right.delivery == "unavailable"
        assert right.delivery_unavailable_reason == "archived"
        selected = session.rights_selected_delivery_offer_snapshot(
            account, **select(),
        )
        assert selected.rights[0].delivery == "unavailable"
        assert selected.rights[0].delivery_offer_id == ""
        assert posts == []
    finally:
        session.close()


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
