"""#4988: original occurrences on a controlled, already admitted C relation.

The fixture relation is explicit Core writer input, not evidence that a
supplier has proved an equivalence. Exercise the real one-shot executor.
"""
from dataclasses import replace

import pytest

from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.core.borrow_for_identity_native_occurrence import stamp_native_borrow_occurrences
from cifereca.core.borrow_for_identity_native_roster import (
    capture_native_lender_roster, native_lender_roster_current,
)
from cifereca.core.notice_c_cover_index import persist_consolidated_cover_source
from cifereca.core.notice_c_private_search_web import (
    _source_key, capture_authenticated_search_call,
)
from cifereca.core.notice_cover_repository import persist_notice_cover
from cifereca.core.notice_source_observation import admit_source_notice
from tests.test_core_p1_4987_cross_profile_executor import BorrowingOffline
from tests.test_core_p1_4662_live_lender_preview import _candidate, harness


def setup(harness, monkeypatch):
    service, account, _ = harness
    # The native private source contract requires an exact nonempty
    # account id. The ordinary Paris fixture uses the empty primary alias.
    account.unique_id = "native-reader"
    session = BorrowingOffline(account, service)
    session.descriptor = replace(
        session.descriptor,
        capabilities=replace(session.descriptor.capabilities, authentication=True),
    )
    service._sessions[service._session_key(
        account.provider_id, account.credential_id,
    )] = session
    connection, = service.storage.provider_connections(identity_id="alex")
    service.storage.provider_account_put({
        "connection_id": connection["connection_id"], "identity_id": "alex",
        "provider_id": account.provider_id, "credential_id": account.credential_id,
        "account_id": account.unique_id, "is_primary": True,
    })
    monkeypatch.setattr(service, "_provider_enabled_for_identity", lambda *_: True)
    first = replace(_candidate().notice, source_account_id=account.unique_id)
    second = replace(first, id="second", record_id="record-2")
    call = capture_authenticated_search_call(service, session, "alex")
    assert call is not None
    stamp_native_borrow_occurrences(service, call, [first, second])
    sources = []
    for row in (first, second):
        key = _source_key(row, "alex")
        obs = admit_source_notice(
            key=key, parser_id="core.search.authenticated-web", parser_version="1",
            observed_at=100, raw_fields={"cover": None},
        )
        sources.append(persist_notice_cover(
            service.storage, obs, field_path=("cover",), cover_inventory_complete=False,
        ))
    c = None
    for source in sources:
        c = persist_consolidated_cover_source(
            service.storage, canonical_edition_id="edition:controlled-native-roster",
            source=source, relation_proof_id="strong:controlled-test",
        )
    roster = capture_native_lender_roster(
        service, document=c.document, expected_epoch=c.epoch,
        requesting_identity_id="default", occurrences=(first, second),
    )
    assert roster is not None and len(roster.rows) == 2
    return service, account, session, first, second, c, roster


def execute(service, roster, selected):
    return service.execute_normal_lender_borrow_for_current_identity(
        captured_candidate=selected, exact_notice=selected._notice,
        captured_roster=roster, confirmed=True,
        expected_requesting_identity_id="default",
        expected_requesting_identity=service.current_identity,
        expected_context_epoch=service._account_context_epoch,
        expected_source_epoch=service._source_context_epoch,
    )


def test_native_roster_executes_selected_occurrence_only(harness, monkeypatch):
    service, account, session, first, second, _, roster = setup(harness, monkeypatch)
    selected = next(row for row in roster.rows if row._notice is second)
    requester = service.current_identity
    assert execute(service, roster, selected)["status"] == "confirmed"
    assert session.actions == 1 and session.callbacks == [True]
    assert service.current_identity is requester
    assert service.storage.loan_borrow_state(borrow_claim_key(account, first)) is None
    assert service.storage.loan_borrow_state(borrow_claim_key(account, second))["status"] == "confirmed"
    assert execute(service, roster, selected)["status"] == "blocked"
    assert session.actions == 1


@pytest.mark.parametrize("change", ["alternative", "epoch", "truncated", "unrelated"])
def test_native_roster_rejects_changed_unselected_source(harness, monkeypatch, change):
    service, account, session, first, second, c, roster = setup(harness, monkeypatch)
    selected = next(row for row in roster.rows if row._notice is second)
    if change == "alternative":
        first.revision_id = "r2"
    elif change == "epoch":
        service._source_context_epoch += 1
    elif change == "truncated":
        roster = replace(roster, rows=(selected,), _sources=roster._sources[-1:])
    else:
        roster = replace(roster, document=replace(c.document, source_key="notice-c:canonical:v1:" + "0" * 64))
    assert not native_lender_roster_current(service, roster)
    assert execute(service, roster, selected)["status"] == "blocked"
    assert session.actions == 0
    assert service.storage.loan_borrow_state(borrow_claim_key(account, second)) is None
