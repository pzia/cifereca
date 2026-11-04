"""An archive prevents delivery, while retaining the exact purchase (#514)."""
from dataclasses import replace
import logging

import pytest

from cifereca.plugin_api import HTTPX_TRANSPORT, ProviderCapabilityError
from cifereca_kobo_vault import KoboVaultError
from cifereca.core.source_convergence import plan_convergence
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
    second[2].accounts = lambda identity_id=None: [type("Ref", (), dict(
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


def test_selected_exact_archive_409_becomes_unavailable_without_revoking_purchase(caplog):
    session, vault, _broker, account, posts = setup()
    calls = []
    private = "PRIVATE_ARCHIVE_UPSTREAM_MESSAGE"

    def archived(product, **options):
        calls.append((product, options.get("mode")))
        raise KoboVaultError(
            "upstream " + private, code="archived_book", status_code=409,
        )

    vault.acquisition = archived
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            report = session.rights_delivery_offer_snapshot(
                account, "book-1", explicit_user_action=True,
            )
        right = report.exact("book-1", "book-1", "purchase")
        assert right is not None and right.state == "active"
        assert right.provider_attested is True
        assert right.evidence_ref == "kobo.personal_manifest"
        assert right.delivery == "unavailable"
        assert right.delivery_unavailable_reason == "archived"
        assert right.delivery_offer_id == ""
        assert not report.authorizes_negative_reconciliation
        assert report.coverage == "partial"
        # Core's provider-neutral plan consumes exactly this closed reason;
        # no Placement can be synthesized from a remote 409 alone.
        plan = plan_convergence((report,))
        assert len(plan.decisions) == 1
        assert plan.decisions[0].state == "skipped_archived"
        assert plan.decisions[0].reason == "delivery_archived"
        assert calls == [("book-1", "descriptor")] and posts == []
        assert vault.calls == 1
        lines = [
            entry.getMessage() for entry in caplog.records
            if "EVENT kobo selected_offer" in entry.getMessage()
        ]
        assert len(lines) == 1
        assert "outcome=unavailable reason=archived_http_409_no_acquisition" in lines[0]
        assert "http_status=409 error_code=archived_book" in lines[0]
        assert private not in lines[0] and "https://" not in lines[0]
    finally:
        session.close()


@pytest.mark.parametrize(("status", "code"), [
    (409, "conflict"), (409, "revision_mismatch"), (403, "archived_book"),
])
def test_unqualified_http_refusal_cannot_fake_archived_delivery(status, code):
    session, vault, _broker, account, posts = setup()
    calls = []
    def blocked(product, **options):
        calls.append((product, options.get("mode")))
        raise KoboVaultError(
            "private upstream", code=code, status_code=status,
        )
    vault.acquisition = blocked
    try:
        with pytest.raises(KoboVaultError) as caught:
            session.rights_delivery_offer_snapshot(
                account, "book-1", explicit_user_action=True,
            )
        assert caught.value.code == code
        assert calls == [("book-1", "descriptor")] and posts == []
    finally:
        session.close()


def test_archive_409_does_not_poison_future_success_after_restoration():
    session, vault, _broker, account, posts = setup()
    original = vault.acquisition
    def archived(_product, **_options):
        raise KoboVaultError("archived", code="archived_book", status_code=409)
    try:
        vault.acquisition = archived
        archived_result = session.rights_delivery_offer_snapshot(
            account, "book-1", explicit_user_action=True,
        )
        assert archived_result.rights[0].delivery_unavailable_reason == "archived"
        vault.acquisition = original
        restored = session.rights_delivery_offer_snapshot(
            account, "book-1", explicit_user_action=True,
        )
        assert restored.rights[0].delivery == "available"
        assert restored.rights[0].delivery_unavailable_reason == ""
        assert len(posts) == 1
    finally:
        session.close()
