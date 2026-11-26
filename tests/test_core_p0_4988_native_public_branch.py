"""#4988: native primary accounts sharing one independently admitted public C."""
from dataclasses import replace

import pytest

from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.core.borrow_for_identity_native_roster import native_lender_roster_current
from cifereca.core.borrow_for_identity_public_branch import (
    capture_public_branch_borrow_call, stamp_public_branch_borrow_response,
    public_branch_occurrence_document,
)
from cifereca.core.notice_c_cover_index import persist_consolidated_cover_source
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public
from tests.test_core_p0_4988_native_roster import execute
from tests.test_core_p1_4987_cross_profile_executor import BorrowingOffline
from tests.test_core_p1_4662_live_lender_preview import harness, _candidate


def install(service, account):
    session = BorrowingOffline(account, service)
    session.descriptor = replace(
        session.descriptor,
        capabilities=replace(session.descriptor.capabilities, authentication=True),
        existing_primary_loan_account_alias="primary",
        authenticated_search_public_edition_branches_v1=(("DILICOM", "EBOOKS", "digital", None),),
    )
    service._sessions[service._session_key(account.provider_id, account.credential_id)] = session
    connection = service.storage.provider_connection_put({
        "identity_id": account.identity_id, "provider_id": account.provider_id,
        "credential_id": account.credential_id, "enabled": True,
    })
    service.storage.provider_account_put({
        "connection_id": connection, "identity_id": account.identity_id,
        "provider_id": account.provider_id, "credential_id": account.credential_id,
        "account_id": "", "is_primary": True,
    })
    return session


def retain(service, session, account, **changes):
    call = capture_public_branch_borrow_call(service, session, None)
    assert call is not None
    row = replace(_candidate(account.credential_id, collection=None).notice, **changes)
    declaration = PublicSearchNotice(replace(
        row, owned=False, downloadable=False, download_url=None, source_account_id=None,
        friendly_url=None, thumbnail_url=None, metadata={}, description=None,
    ))
    stamp_public_branch_borrow_response(
        service, call, ProviderSearchResponse([row], public_notices=(declaration,)),
    )
    return row


def setup(harness, monkeypatch):
    service, alex, _ = harness
    bob = replace(alex, identity_id="bob", credential_id="card-bob")
    identity = replace(service.identity_by_id("alex"), identity_id="bob", label="Bob",
                       directory_name="bob")
    service.identities = [*service.identities, identity]
    service.storage.identity_put(identity)
    service.config.upsert_identity({
        "id": "bob", "label": "Bob", "directory_name": "bob",
        "primary_paris_connection_id": "card-bob",
    })
    service.config.upsert_provider_account({
        "id": "card-bob", "provider": "paris", "identity_id": "bob", "enabled": True,
    })
    service.accounts = [alex, bob]
    monkeypatch.setattr(service, "_provider_enabled_for_identity", lambda *_: True)
    a, b = install(service, alex), install(service, bob)
    source = persist(service.storage, verified_public(
        source_provider="paris", record_id="record-1", docbase="DILICOM", scenario="EBOOKS",
    ))
    c = persist_consolidated_cover_source(
        service.storage, canonical_edition_id=source.observation.key.edition_id,
        source=source, relation_proof_id="self:exact-public-source",
    )
    return service, alex, bob, a, b, c


def discover(service, c):
    return service.possible_borrow_lenders_for_c_for_current_identity(
        document=c.document, expected_epoch=c.epoch,
    )


def test_native_empty_primary_roster_zero_one_many_and_exact_dispatch(harness, monkeypatch):
    service, alex, bob, a, b, c = setup(harness, monkeypatch)
    assert discover(service, c) is None
    first = retain(service, a, alex)
    assert len(discover(service, c).rows) == 1
    second = retain(service, b, bob)
    roster = discover(service, c)
    assert len(roster.rows) == 2
    assert {id(row._notice) for row in roster.rows} == {id(first), id(second)}
    assert a.calls == b.calls == a.actions == b.actions == 0
    selected = next(row for row in roster.rows if row._notice is second)
    assert execute(service, roster, selected)["status"] == "confirmed"
    assert a.actions == 0 and b.actions == 1
    assert service.storage.loan_borrow_state(borrow_claim_key(alex, first)) is None
    assert service.storage.loan_borrow_state(borrow_claim_key(bob, second))["status"] == "confirmed"
    assert execute(service, roster, selected)["status"] == "blocked"


@pytest.mark.parametrize("change", ["branch", "revision", "mirror", "foreign", "superseded"])
def test_public_branch_does_not_rebind_or_infer_native_occurrences(harness, monkeypatch, change):
    service, alex, bob, a, b, c = setup(harness, monkeypatch)
    first, second = retain(service, a, alex), retain(service, b, bob)
    roster = discover(service, c)
    assert roster is not None
    selected = next(row for row in roster.rows if row._notice is second)
    if change == "branch":
        first.scenario = "CATALOGUE"
    elif change == "revision":
        first.revision_id = "different-edition"
    elif change == "mirror":
        first.metadata = {"catalogue_access": "shared_read_only"}
    elif change == "foreign":
        a.connected = False
    else:
        replacement = retain(service, a, alex)
        assert replacement is not first
    assert public_branch_occurrence_document(service, first, owner="alex") is None
    assert not native_lender_roster_current(service, roster)
    assert execute(service, roster, selected)["status"] == "blocked"
    assert a.actions == b.actions == 0
