"""An archive prevents delivery, while retaining the exact purchase (#514)."""
from dataclasses import replace

import pytest

from cifereca.plugin_api import HTTPX_TRANSPORT, ProviderCapabilityError
from tests.test_p1_3252_kobo_selected_delivery_offer import setup
from tests.test_p1_3252_kobo_selected_candidate import admitted, execute


@pytest.mark.parametrize("downloadable", [True, False, None])
def test_archived_purchase_stays_active_without_descriptor(downloadable):
    session, vault, broker, account, posts = setup()
    vault.rows[0].update(archived=True, downloadable=downloadable)
    try:
        for _ in range(2):
            report = session.rights_delivery_offer_snapshot(
                account, "book-1", explicit_user_action=True,
            )
            right = report.exact("book-1", "book-1", "purchase")
            assert right.state == "active" and right.provider_attested
            assert right.delivery == "unavailable" and not right.delivery_offer_id
            assert right.delivery_unavailable_reason == "archived"
            assert report.coverage == "partial"
            assert not report.authorizes_negative_reconciliation
        assert vault.calls == 2 and posts == []
    finally:
        session.close()


def test_selected_archived_right_is_a_non_error_without_post():
    session, vault, broker, account, posts = setup()
    vault.rows[0]["archived"] = True
    try:
        result = session.rights_selected_delivery_offer_snapshot(
            account, edition_id="book-1", right_id="book-1",
            kind="purchase", branch="personal_manifest",
            explicit_user_action=True,
        )
        assert len(result.rights) == 1
        right = result.rights[0]
        assert right.state == "active"
        assert right.delivery == "unavailable"
        assert right.delivery_unavailable_reason == "archived"
        assert right.delivery_offer_id == ""
        assert vault.calls == 1 and posts == []
    finally:
        session.close()


def test_archive_since_offer_prevents_post_and_content_get(tmp_path, monkeypatch):
    session, vault, broker, account, posts = setup()
    def unexpected(**kwargs):
        pytest.fail("Archived purchase must not open a content client")
    monkeypatch.setattr(HTTPX_TRANSPORT, "public_client", unexpected)
    try:
        offer = admitted(session, account)
        assert len(posts) == 1
        vault.rows[0]["archived"] = True
        with pytest.raises(ProviderCapabilityError, match="delivery_unavailable"):
            execute(session, account, offer, tmp_path)
        assert len(posts) == 1 and vault.calls == 2
        assert list(tmp_path.iterdir()) == []
    finally:
        session.close()


def test_archive_is_scoped_to_account_not_product_id():
    first = setup()
    second = setup()
    first[1].rows[0]["archived"] = True
    # Two independent account scopes can own the same product, one archived.
    second[0]._accounts[0].unique_id = "bob"
    second[1].rows = [dict(second[1].rows[0], account_id="bob")]
    second[2].accounts = lambda: [type("Ref", (), dict(
        provider_id="kobo", account_id="bob", identity_id="identity-B",
    ))()]
    other = replace(second[3], unique_id="bob", identity_id="identity-B")
    try:
        archived = first[0].rights_observation_snapshot(first[3])
        available = second[0].rights_observation_snapshot(other)
        assert archived.rights[0].delivery == "unavailable"
        assert archived.rights[0].delivery_unavailable_reason == "archived"
        assert available.rights[0].delivery == "unknown"
        assert available.rights[0].delivery_unavailable_reason == ""
        assert archived.rights[0].state == available.rights[0].state == "active"
        assert archived.scope != available.scope
        assert first[4] == second[4] == []
    finally:
        first[0].close()
        second[0].close()


@pytest.mark.parametrize("flag", ["true", 1])
def test_invalid_archive_flag_cannot_be_a_positive_purchase(flag):
    session, vault, broker, account, posts = setup()
    vault.rows[0]["archived"] = flag
    try:
        report = session.rights_observation_snapshot(account)
        assert report.exact("book-1", "book-1", "purchase") is None
        assert posts == []
    finally:
        session.close()
