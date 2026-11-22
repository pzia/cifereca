"""#4030: one explicit typed private inventory read using synthetic transport."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core import local_origin_remote_loans as remote
from cifereca.core.local_origin_suggestion_operator import run_local_origin_suggestion
from cifereca.plugin_api.loan_inventory import (
    LoanInventoryScope, LoanInventorySnapshot, LoanBranchCoverage, read_loan_inventory,
)
from tests.test_core_p0_4030_private_catalogue import private_rig, rig, store, rows


@pytest.fixture
def case(private_rig, tmp_path, monkeypatch):
    service, request, account, session, calls, path, key, snap = private_rig
    session.capabilities = NS(loans=True)
    session.loans = lambda *_: pytest.fail("legacy fallback not admitted")
    session.search = lambda *_: pytest.fail("no local title sent")
    session.download = lambda *_: pytest.fail("no delivery")
    # The remote catalogue may exist before any exact operational A is cached.
    with service.storage._lock, service.storage._connect() as db:
        db.execute("DELETE FROM source_references")
    scope = LoanInventoryScope.for_account(account)
    observed = replace(service.last_loans[0], id="fresh-loan")
    def reader(selected):
        assert selected is account
        calls.append("typed_inventory")
        return LoanInventorySnapshot(
            scope, (observed,), status="success",
            branches=(LoanBranchCoverage("loans", "digital", "complete", True),),
            observed_at="2026-01-01T00:00:00Z",
        )
    session.loan_inventory_snapshot = reader
    monkeypatch.setattr(remote, "acquisition_lock_file", lambda: tmp_path / "acquire.lock")
    known = run_local_origin_suggestion(service, request)
    evidence = known["public_comparison_evidence"]
    choice, = remote.available_remote_loan_catalogues(service, request, evidence=evidence)
    assert not calls
    return service, request, account, session, path, calls, choice, observed


def test_remote_loan_review_only_sends_selected_account_and_never_persists(case):
    service, request, account, session, path, calls, choice, observed = case
    before, data = rows(service.storage), path.read_bytes()
    memory = list(service.last_loans)
    assert remote.compare_remote_loan_catalogue(service, choice)["status"] == "excluded"
    assert calls == []
    result = remote.compare_remote_loan_catalogue(service, choice, confirmed=True)
    assert result["status"] == "partial"
    match, = result["candidates"]
    assert match.loan_id == "fresh-loan" and match.edition_id == observed.record_id
    assert match.comparison == "title_author_concordant"
    assert not hasattr(match, "source") and not hasattr(match, "branch")
    assert result["observed_at"] == "2026-01-01T00:00:00Z"
    assert result["provider_read_requested"] is True
    assert not any(result[k] for k in ("coverage_complete", "fresh_network_verified",
                                      "association_authorized", "may_write_origin_or_right",
                                      "local_description_transmitted"))
    assert calls == ["typed_inventory"] and service.last_loans == memory
    assert rows(service.storage) == before and path.read_bytes() == data


@pytest.mark.parametrize("change", ["disabled", "session", "account", "reader", "missing_reader",
                                    "file", "navigation", "cancel", "sql_borrower"])
def test_stale_or_unqualified_remote_choice_never_requests_inventory(case, change):
    service, request, account, session, path, calls, choice, observed = case
    opts = {}
    if change == "disabled":
        service.sync_source_enabled = lambda _: False
    elif change == "session":
        service._sessions["private"] = NS(**vars(session))
    elif change == "account":
        service.accounts = [replace(account)]
    elif change == "reader":
        session.loan_inventory_snapshot = lambda _: pytest.fail("different reader")
    elif change == "missing_reader":
        del session.loan_inventory_snapshot
    elif change == "file":
        path.write_bytes(b"changed")
    elif change == "navigation":
        service._account_context_epoch += 2
    elif change == "cancel":
        opts["still_current"] = lambda: False
    else:
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DELETE FROM provider_accounts")
    result = remote.compare_remote_loan_catalogue(service, choice, confirmed=True, **opts)
    assert result["status"] != "partial" and not result["candidates"]
    assert not result["provider_read_requested"] and not calls


@pytest.mark.parametrize("change", ["session", "file", "wrong_scope", "private_error", "duplicate"])
def test_discard_remote_response_after_drift_or_ambiguity(case, change, monkeypatch):
    service, request, account, session, path, calls, choice, observed = case
    original = remote.read_loan_inventory
    def read(*args, **kwargs):
        value = original(*args, **kwargs)
        if change == "session":
            service._sessions["private"] = NS(**vars(session))
        elif change == "file":
            path.write_bytes(b"changed")
        elif change == "wrong_scope":
            return LoanInventorySnapshot(
                replace(value.scope, account_id="foreign"), (), status="success")
        elif change == "private_error":
            raise RuntimeError("PRIVATE upstream body")
        else:
            return replace(value, loans=(observed, replace(observed, record_id="other-edition")))
        return value
    monkeypatch.setattr(remote, "read_loan_inventory", read)
    before = rows(service.storage)
    result = remote.compare_remote_loan_catalogue(service, choice, confirmed=True)
    assert not result["candidates"] and result["provider_read_requested"]
    assert "PRIVATE" not in repr(result) and calls == ["typed_inventory"]
    assert rows(service.storage) == before


def test_missing_edition_is_not_inferred_and_typed_only_has_no_legacy_fallback(case):
    service, request, account, session, path, calls, choice, observed = case
    scope = LoanInventoryScope.for_account(account)
    report = LoanInventorySnapshot(scope, (replace(observed, record_id=""),))
    matches, truncated = remote._matches(report, choice, "Roman commun", ("Autrice",))
    assert len(matches) == 1 and matches[0].edition_id is None and not truncated
    del session.loan_inventory_snapshot
    assert read_loan_inventory(session, account, typed_only=True).status == "failed"
    assert calls == []


@pytest.mark.parametrize("change", ["none", "view", "activation", "new_preview", "cancel",
                                    "before_worker", "after_worker"])
def test_remote_inventory_has_own_one_use_action_in_detail_and_revocation(case, change):
    from tests.test_core_p0_7092_origin_detail_controller import mounted, deliver
    service, request, account, session, path, calls, choice, observed = case
    before, data = rows(service.storage), path.read_bytes()
    c, dispose, host, active, tasks, actions, groups, toasts = mounted(
        (service, session, request, calls, path))
    assert set(actions) == {"preview"} and not calls and not tasks
    assert actions["preview"][2]()
    deliver(tasks)
    original = c.page
    remote_choice, = original.remote_loan_choices
    title, tooltip, invoke = actions["remote-loan-0"]
    assert "Relire les prêts" in title and "aucun titre local transmis" in tooltip
    assert "seller" in tooltip and "card-A" in tooltip and "buyer-A" in tooltip
    assert not calls and len(tasks) == 1
    assert c.broker.compare_remote_loan(token=remote_choice.token) is None
    assert c.broker.compare_remote_loan(token="forged", confirmed=True) is None
    if change == "view":
        host.live = False
    elif change == "activation":
        active[0] = False
    elif change == "new_preview":
        new = c.broker.preview(identity_id=request.identity_id,
                               placement_id=request.placement_id,
                               still_current=lambda: True, confirmed=True)
        assert new.remote_loan_choices[0].token != remote_choice.token
    elif change == "cancel":
        c.broker.cancel(token=original.token)
    invoked = invoke()
    if change in ("view", "activation"):
        assert not invoked
    else:
        assert invoked and not invoke()
        work, done = tasks[-1]
        if change == "before_worker":
            host.live = False
        result = work()
        if change == "after_worker":
            active[0] = False
        count = len(groups)
        done(result)
        done(result)
        if change == "none":
            assert len(groups) == count + 2
            assert groups[-1][0] == "Classement documentaire — lecture seule"
            assert groups[-2][0] == "Inventaire privé de prêts — lecture seule"
            text = repr(groups[-2])
            assert "Occurrence : fresh-loan" in text and "Branche canonique non attestée" in text
            assert "titre et fichier locaux non transmis" in text
        else:
            assert len(groups) == count
    assert calls == (["typed_inventory"] if change in ("none", "after_worker") else [])
    assert c.broker.compare_remote_loan(token=remote_choice.token, confirmed=True) is None
    assert rows(service.storage) == before and path.read_bytes() == data


def test_remote_loan_choices_share_the_live_host_action_budget(case):
    from tests.test_core_p0_7092_origin_detail_controller import mounted, deliver
    from cifereca.plugin_api.local_origin_detail import (
        LocalOriginRemoteLoanChoice, LocalOriginPrivateChoice, LocalOriginPublicChoice,
    )
    service, request, account, session, path, calls, choice, observed = case
    c, dispose, host, active, tasks, actions, groups, toasts = mounted(
        (service, session, request, calls, path))
    page = c.broker.preview(identity_id=request.identity_id, placement_id=request.placement_id,
                            still_current=lambda: True, confirmed=True)
    public = tuple(LocalOriginPublicChoice(f"pub-{i}", f"provider-{i}") for i in range(8))
    private = tuple(LocalOriginPrivateChoice(f"cache-{i}", "seller", "card",
                                             f"account-{i}", "loan") for i in range(16))
    remote_choices = tuple(LocalOriginRemoteLoanChoice(f"remote-{i}", "seller", "card",
                                                        f"account-{i}") for i in range(16))
    c.broker = NS(preview=lambda **kw: replace(
        page, public_choices=public, private_choices=private,
        remote_loan_choices=remote_choices))
    host.retire_action = lambda key: actions.pop(key, None) is not None
    add = host.add_action
    def limited(key, title, tooltip, action):
        assert len(actions) < 12 and len(title) <= 256 and len(tooltip) <= 1024
        add(key, title, tooltip, action)
    host.add_action = limited
    assert actions["preview"][2]()
    deliver(tasks)
    seen = set()
    for _ in range(5):
        seen.update(k for k in actions if k.startswith(("public-", "private-", "remote-loan-")))
        more = [v[2] for k, v in actions.items() if k.startswith("catalogue-more-")]
        if not more:
            break
        assert more[0]()
        deliver(tasks)
    assert seen == ({f"public-{i}" for i in range(8)}
                    | {f"private-{i}" for i in range(16)}
                    | {f"remote-loan-{i}" for i in range(16)})
    assert not calls and not toasts


@pytest.mark.parametrize("twin", ["physical", "invalid_edition", "other_title"])
def test_remote_occurrence_ambiguity_survives_descriptive_filter(case, twin):
    service, request, account, session, path, calls, choice, observed = case
    scope = LoanInventoryScope.for_account(account)
    other = (replace(observed, medium="physical") if twin == "physical"
             else replace(observed, record_id="x" * 257) if twin == "invalid_edition"
             else replace(observed, title="Different title"))
    report = LoanInventorySnapshot(scope, (observed, other))
    before, data = rows(service.storage), path.read_bytes()
    matches, truncated = remote._matches(report, choice, "Roman commun", ("Autrice",))
    assert matches == () and not truncated
    assert not calls and rows(service.storage) == before and path.read_bytes() == data


def test_remote_loan_evidence_priority_preserves_ties_and_window_limit(
    case, monkeypatch,
):
    service, request, account, session, path, calls, choice, observed = case
    scope = LoanInventoryScope.for_account(account)
    snapshot = LoanInventorySnapshot(scope, (
        replace(observed, id="divergent", author="Other author"),
        replace(observed, id="exact-a", author="Autrice"),
        replace(observed, id="unknown", author=None),
        replace(observed, id="exact-b", author="Autrice"),
    ))
    before, raw = rows(service.storage), path.read_bytes()
    matches, truncated = remote._matches(snapshot, choice, "Roman commun", ("Autrice",))
    assert [(item.loan_id, item.comparison) for item in matches] == [
        ("exact-a", "title_author_concordant"),
        ("exact-b", "title_author_concordant"),
        ("unknown", "title_concordant_author_unknown"),
        ("divergent", "title_concordant_author_divergent"),
    ]
    assert not truncated
    # Rank before the bounded window cap: weak evidence must not hide exact
    # matches when a supplier places a divergent description first.
    monkeypatch.setattr(remote, "_MAX_MATCHES", 2)
    limited, truncated = remote._matches(snapshot, choice, "Roman commun", ("Autrice",))
    assert [item.loan_id for item in limited] == ["exact-a", "exact-b"]
    assert truncated
    assert not calls and rows(service.storage) == before and path.read_bytes() == raw


@pytest.mark.parametrize("coordinate", ["id", "record_id"])
@pytest.mark.parametrize("spoof", ["\x85", "\u202e", "\u2028", "\u200d", "\u2066"])
def test_remote_loan_result_suppresses_spoofed_occurrence_coordinates(
    case, coordinate, spoof,
):
    service, request, account, session, path, calls, choice, observed = case
    changes = {"id": "other-loan"}
    changes[coordinate] = ("other-loan" if coordinate == "id" else "other-edition") + spoof + "FORGED"
    forged = replace(observed, **changes)
    snapshot = LoanInventorySnapshot(
        LoanInventoryScope.for_account(account), (forged, observed),
        status="success",
    )
    before, raw = rows(service.storage), path.read_bytes()
    matches, truncated = remote._matches(
        snapshot, choice, "Roman commun", ("Autrice",),
    )
    assert [row.loan_id for row in matches] == ["fresh-loan"]
    assert not truncated and not calls
    assert rows(service.storage) == before and path.read_bytes() == raw


@pytest.mark.parametrize("spoof", ["\x85", "\u202e", "\u2028", "\u200d", "\u2066"])
def test_remote_account_discovery_does_not_bind_spoofed_scope(
    case, monkeypatch, spoof,
):
    service, request, account, session, path, calls, choice, observed = case
    malformed = replace(account, unique_id=account.unique_id + spoof + "FORGED")
    service.accounts[:] = [malformed]
    session.accounts[:] = [malformed]
    def forbidden(*_args, **_kwargs):
        pytest.fail("Non-displayable account is ineligible before session binding")
    monkeypatch.setattr(remote, "_binding", forbidden)
    before, contents = rows(service.storage), path.read_bytes()
    assert remote.available_remote_loan_catalogues(
        service, request, evidence=choice.evidence,
    ) == ()
    assert not calls and rows(service.storage) == before and path.read_bytes() == contents
