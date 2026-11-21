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


@pytest.mark.parametrize("change", ["none", "view", "activation", "cancel", "new_preview",
                                    "session", "account", "same_bytes_new_inode", "disabled"])
def test_private_choice_is_one_shot_exact_and_read_only(private_rig, change):
    from cifereca.core.local_origin_detail_broker import LocalOriginDetailReadFactory
    service, request, account, session, calls, path, key, snap = private_rig
    active, visible = [True], [True]
    broker = LocalOriginDetailReadFactory(service).for_activation("fixture", current=lambda: active[0])
    def preview():
        return broker.preview(identity_id="A", placement_id=request.placement_id,
                              still_current=lambda: visible[0], confirmed=True)
    before, data = rows(service.storage), path.read_bytes()
    page = preview()
    choice, = page.private_choices
    assert (choice.provider_id, choice.credential_id, choice.account_id, choice.kind) == (
        "seller", "card-A", "buyer-A", "loan")
    assert broker.compare_private(token=choice.token) is None
    assert broker.compare_private(token="forged", confirmed=True) is None
    assert not calls
    if change == "view":
        visible[0] = False
    elif change == "activation":
        active[0] = False
    elif change == "cancel":
        broker.cancel(token=page.token)
    elif change == "new_preview":
        assert preview().private_choices[0].token != choice.token
    elif change == "session":
        service._sessions["private"] = NS(**vars(session))
    elif change == "account":
        other = replace(account)
        service.accounts = [other]
        session.accounts = [other]
    elif change == "same_bytes_new_inode":
        path.rename(path.with_suffix(".old"))
        path.write_bytes(data)
    elif change == "disabled":
        service.sync_source_enabled = lambda a: False
    report = broker.compare_private(token=choice.token, confirmed=True)
    if change == "none":
        assert report.status == "partial"
        match, = report.matches
        assert match.title == "Roman commun" and match.comparison == "title_author_concordant"
        assert (match.branch, match.resource_id) == (key.branch, key.right_id)
        assert match.observed_at is None
        assert not any((report.coverage_complete, report.fresh_network_verified,
                        report.provider_access_performed, report.association_authorized))
    else:
        assert report is None or (report.status != "partial" and not report.matches)
    assert broker.compare_private(token=choice.token, confirmed=True) is None
    assert calls == [] and rows(service.storage) == before and path.read_bytes() == data


def test_invalid_sibling_account_does_not_hide_exact_private_choice(private_rig):
    from cifereca.core.local_origin_detail_broker import LocalOriginDetailReadFactory
    service, request, account, session, calls, path, key, snap = private_rig
    service.accounts.insert(0, replace(account, provider_id=""))
    broker = LocalOriginDetailReadFactory(service).for_activation("fixture", current=lambda: True)
    page = broker.preview(identity_id="A", placement_id=request.placement_id,
                          still_current=lambda: True, confirmed=True)
    choice, = page.private_choices
    assert choice.provider_id == "seller" and not calls


@pytest.mark.parametrize("change", ["none", "view", "activation", "before_worker"])
def test_installed_detail_controller_private_action_explicit_exact_and_revocable(private_rig, change):
    from tests.test_core_p0_7092_origin_detail_controller import mounted, deliver
    service, request, account, session, calls, path, key, snap = private_rig
    before, data = rows(service.storage), path.read_bytes()
    c, dispose, host, active, tasks, actions, groups, toasts = mounted(
        (service, session, request, calls, path))
    assert set(actions) == {"preview"} and not tasks and not calls
    assert actions["preview"][2]()
    deliver(tasks)
    private_action = actions["private-0"][2]
    assert not calls and len(tasks) == 1
    assert private_action() and not private_action()
    work, done = tasks[-1]
    if change == "before_worker":
        host.live = False
    result = work()
    if change == "view":
        host.live = False
    elif change == "activation":
        active[0] = False
    count = len(groups)
    done(result)
    done(result)
    if change == "none":
        assert len(groups) == count + 1
        visible = repr(groups[-1])
        assert "Occurrence : loan-1" in visible and "Branche : loans" in visible
        assert "Date de description inconnue" in visible
    else:
        assert len(groups) == count
    assert not calls and rows(service.storage) == before and path.read_bytes() == data


def test_cached_purchase_date_is_source_date_not_refresh_time(private_rig, monkeypatch):
    from tests.test_core_p0_7092_origin_detail_controller import mounted, deliver
    service, request, account, session, calls, path, _, _ = private_rig
    key, snap = new_source()
    persist_attested_source_references(service.storage, snap)
    monkeypatch.setattr(private, "_read_descriptive_txn", lambda db, source: NS(
        facts=(NS(field="title", value="Roman commun", observed_at=100),
               NS(field="author", value="Autrice", observed_at=100)), alternatives=()))
    c, dispose, host, active, tasks, actions, groups, toasts = mounted(
        (service, session, request, calls, path))
    actions["preview"][2]()
    deliver(tasks)
    assert "achats" in actions["private-0"][0]
    assert actions["private-0"][2]()
    deliver(tasks)
    visible = repr(groups[-1])
    assert "1970-01-01 00:01 UTC" in visible
    assert "Occurrence : " + key.right_id in visible
    assert "fraîcheur réseau" not in visible and not calls
