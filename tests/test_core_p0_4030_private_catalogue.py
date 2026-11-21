"""#4030 private known catalogues, explicit scope and no provider dispatch."""
from dataclasses import replace
from types import SimpleNamespace as NS
import pytest

from cifereca.core import local_origin_private_catalogue as private
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.models import Account, Loan
from tests.test_core_p0_4030_public_catalogue import rig, store, rows
from tests.test_core_p0_3171_atomic_publication_v14 import new_source, register


@pytest.fixture
def private_rig(rig):
    service, _, request, calls, path = rig
    account = Account(identity_id="A", provider_id="seller", credential_id="card-A",
                      unique_id="buyer-A")
    session = NS(provider_id="seller", credential_id="card-A", connected=True,
                 accounts=[account])
    service.accounts = [account]
    service._sessions["private"] = session
    service.sync_source_enabled = lambda a: a is account
    service._provider_enabled_for_identity = lambda p, i: p == "seller" and i == "A"
    key, snap = new_source(kind="loan", right="loan-1", branch="loans")
    persist_attested_source_references(service.storage, snap)
    loan = Loan(id="loan-1", record_id="edition", provider="synthetic",
                medium="digital", title="Roman commun", author="Autrice",
                source_provider="seller", source_credential="card-A",
                source_account_id="buyer-A", identity_id="A")
    service.last_loans = [loan, replace(loan, source_account_id="another-borrower",
                                        title="FOREIGN")]
    return service, request, account, session, calls, path, key, snap


def _run(fixture, **kwargs):
    service, request, account, *_ = fixture
    return private.compare_known_private_catalogue(
        service, request, **{"account": account, "kind": "loan", "confirmed": True, **kwargs},
    )


def test_private_loan_comparison_is_partial_read_only_and_exact(private_rig):
    service, request, account, session, calls, path, key, _ = private_rig
    before, data = rows(service.storage), path.read_bytes()
    result = _run(private_rig)
    assert result["status"] == "partial"
    candidate, = result["candidates"]
    assert candidate.source.scope == key.scope and candidate.source.branch == key.branch
    assert candidate.source.edition_id == key.edition_id and candidate.source.resource_id == key.right_id
    assert candidate.comparison == "title_author_concordant"
    assert candidate.evidence == "cached_loan_description" and candidate.title_observed_at is None
    assert not any(result[k] for k in ("coverage_complete", "fresh_network_verified",
                                     "provider_access_performed", "association_authorized",
                                     "may_write_origin_or_right"))
    assert calls == [] and rows(service.storage) == before and path.read_bytes() == data


@pytest.mark.parametrize("change", ["confirm", "disabled", "foreign", "disconnected",
                                    "duplicate_session", "duplicate_account", "changed_file"])
def test_unadmitted_private_account_cannot_return_candidates(private_rig, change):
    service, request, account, session, calls, path, *_ = private_rig
    options = {}
    if change == "confirm":
        options["confirmed"] = False
    elif change == "disabled":
        service.sync_source_enabled = lambda a: False
    elif change == "foreign":
        options["account"] = replace(account, identity_id="B")
    elif change == "disconnected":
        session.connected = False
    elif change == "duplicate_session":
        service._sessions["duplicate"] = session
    elif change == "duplicate_account":
        service.accounts.append(replace(account))
    else:
        path.write_bytes(b"changed")
    result = _run(private_rig, **options)
    assert result["status"] in ("excluded", "unavailable")
    assert not result["candidates"] and not calls


def test_two_loan_branches_are_not_inferred_from_branchless_projection(private_rig):
    service, request, account, session, calls, path, key, snap = private_rig
    persist_attested_source_references(service.storage, replace(snap, branch="other"))
    result = _run(private_rig)
    assert result["status"] == "partial" and not result["candidates"] and calls == []


def test_privacy_and_rebinding_through_cached_reader(private_rig, monkeypatch):
    service, request, account, session, calls, path, *_ = private_rig
    original = private._snapshot
    def changed(*args):
        value = original(*args)
        session.accounts = [replace(account, unique_id="another-borrower")]
        return value
    monkeypatch.setattr(private, "_snapshot", changed)
    result = _run(private_rig)
    assert result["status"] == "scope_changed"
    assert not result["candidates"] and not calls


def test_purchases_reuse_exact_c_reader_without_fabricating_a_notice(private_rig, monkeypatch):
    service, request, account, session, calls, path, *_ = private_rig
    key, snap = new_source()
    persist_attested_source_references(service.storage, snap)
    queried = []
    def exact_reader(db, source):
        queried.append(source)
        return NS(facts=(NS(field="title", value="Roman commun", observed_at=100),
                         NS(field="author", value="Autrice", observed_at=100)), alternatives=())
    monkeypatch.setattr(private, "_read_descriptive_txn", exact_reader)
    before = rows(service.storage)
    result = _run(private_rig, kind="purchase")
    candidate, = result["candidates"]
    assert candidate.source.scope == key.scope and candidate.source.kind == "purchase"
    assert candidate.evidence == "cached_public_purchase_description"
    assert candidate.title_observed_at == 100 and candidate.comparison == "title_author_concordant"
    assert queried == [candidate.source, candidate.source]
    assert not calls and rows(service.storage) == before
