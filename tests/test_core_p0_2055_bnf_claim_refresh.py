"""#2055 / #4082: spent BnF manual claim cannot be bypassed by a fresh row.

All tests are offline and use the existing synthetic Core provider. The
strict no-replay policy remains until P1 #4082 implements versioned cycles.
"""
from dataclasses import replace

from cifereca.core.borrow_action_scope import borrow_claim_key
from tests.test_core_p0_2055_manual_dispatch import setup


def attempt(service, account, item):
    return service.borrow_manual(
        item, expected_identity_id=account.identity_id,
        expected_account=account,
        expected_context_epoch=service._account_context_epoch,
        confirmed=True,
    )


def test_catalogue_refresh_cannot_reopen_confirmed_edition(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        assert service.can_borrow_manual(item)
        first = attempt(service, account, item)
        assert first["status"] == "confirmed"
        assert len(session.dispatches) == 1

        # A new SearchResult.id is a UI row identity, not a new BnF edition.
        refreshed = replace(item, id="bnf-pret:new-search-row-id")
        assert borrow_claim_key(account, item) == borrow_claim_key(account, refreshed)
        assert service.can_borrow_manual(refreshed) is False
        second = attempt(service, account, refreshed)
        assert second["status"] == "verification_unavailable"
        assert second["reason_code"] == "borrow_previous_attempt"
        assert len(session.dispatches) == 1

        # A distinct upstream edition must not be hidden by another key.
        other = replace(item, id="bnf-pret:another-row",
                        record_id="another-edition")
        assert borrow_claim_key(account, other) != borrow_claim_key(account, item)
        assert service.can_borrow_manual(other) is True
    finally:
        service.close()


def test_timeout_and_fresh_search_row_never_retry(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        session.failure = TimeoutError("private-transport-error")
        first = attempt(service, account, item)
        assert first["status"] == "verification_unavailable"
        assert first["provider_verified"] is False
        assert len(session.dispatches) == 1

        fresh = replace(item, id="bnf-pret:after-research")
        assert service.can_borrow_manual(fresh) is False
        retry = attempt(service, account, fresh)
        assert retry["reason_code"] == "borrow_previous_attempt"
        assert len(session.dispatches) == 1
        assert service.storage.loan_borrow_state(
            borrow_claim_key(account, item))["status"] == "verification_unavailable"
    finally:
        service.close()


def test_restart_with_recreated_session_preserves_spent_claim(tmp_path):
    first, session, account, item = setup(tmp_path)
    try:
        assert attempt(first, account, item)["status"] == "confirmed"
        assert len(session.dispatches) == 1
    finally:
        first.close()

    # New LibraryService, fresh provider object, same durable SQLite.
    second, new_session, same_account, new_item = setup(tmp_path)
    try:
        refreshed = replace(new_item, id="bnf-pret:after-restart")
        assert second.can_borrow_manual(refreshed) is False
        outcome = attempt(second, same_account, refreshed)
        assert outcome["reason_code"] == "borrow_previous_attempt"
        assert new_session.dispatches == []
        assert second.storage.loan_borrow_state(
            borrow_claim_key(same_account, refreshed))["status"] == "confirmed"
    finally:
        second.close()


def test_malformed_checkpoint_remains_spent_after_catalogue_refresh(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        key = borrow_claim_key(account, item)
        service.storage.metadata_set(key, "corrupt-record")
        assert service.storage.loan_borrow_state(key) is None
        fresh = replace(item, id="bnf-pret:changed-ui-row")
        assert service.can_borrow_manual(fresh) is False
        outcome = attempt(service, account, fresh)
        assert outcome["reason_code"] == "borrow_previous_attempt"
        assert session.dispatches == []
    finally:
        service.close()
