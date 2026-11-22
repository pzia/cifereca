"""#4988: discover *possible* lenders without two invented permission facts.

All cases use the real in-memory Core/session/SQLite fixture and no provider I/O.
Candidates are inert; executor/POST integration is a distinct tranche.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.borrow_for_identity_discovery import (
    PossibleBorrowLender, discover_possible_borrow_lenders,
)
from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.models import Account
from tests.test_core_p1_4662_live_lender_preview import (
    ParisOffline, _candidate, harness,
)


def _discover(service, *notices, requester="default"):
    return discover_possible_borrow_lenders(
        service, requesting_identity_id=requester, notices=tuple(notices),
    )


def test_normal_borrow_lender_visible_without_prior_positive_eligibility(harness):
    service, account, session = harness
    # The only input is the already-loaded exact search notice, not a
    # fabricated BorrowerOperationEvidence or edition permission.
    notice = _candidate().notice
    before = service._account_context_epoch, service._source_context_epoch
    choices = _discover(service, notice)
    assert len(choices) == 1
    candidate, = choices
    assert type(candidate) is PossibleBorrowLender
    assert candidate.lender_identity_id == "alex"
    assert candidate.label == "Alex sur Paris"
    assert candidate.supplier_eligibility == "not_verified"
    assert candidate.executable is False
    assert candidate._account is account and candidate._notice is notice
    assert "card-alex" not in repr(candidate)
    assert "record-1" not in repr(candidate)
    assert session.calls == 0
    assert service.current_identity.identity_id == "default"
    assert (service._account_context_epoch, service._source_context_epoch) == before


@pytest.mark.parametrize("variant", [
    "no_notices", "source_missing", "wrong_requester",
    "disconnected", "inactive_plugin", "foreign_session",
    "disabled_binding", "claim_spent", "not_primary",
])
def test_nonqualifying_or_spent_lender_never_becomes_local_candidate(
    harness, variant,
):
    service, account, session = harness
    notice = _candidate().notice
    if variant == "no_notices":
        assert _discover(service) == ()
        return
    if variant == "source_missing":
        notice = replace(notice, source_credential="foreign")
    elif variant == "wrong_requester":
        assert _discover(service, notice, requester="alex") == ()
        return
    elif variant == "disconnected":
        session.connected = False
    elif variant == "inactive_plugin":
        old = service._plugin_manager.status
        service._plugin_manager.status = lambda pid: NS(
            **{**vars(old(pid)), "enabled": False}
        )
    elif variant == "foreign_session":
        service._sessions[service._session_key("paris", account.credential_id)] = object()
    elif variant == "disabled_binding":
        service.storage.provider_connection_put({
            "identity_id": "alex", "provider_id": "paris",
            "credential_id": "card-alex", "label": "Fictive",
            "enabled": False, "metadata": {},
        })
    elif variant == "claim_spent":
        key = borrow_claim_key(account, notice)
        assert key is not None
        claimed, _receipt = service.storage.loan_borrow_claim_once(
            key, "one-atomic-attempt",
        )
        assert claimed
    elif variant == "not_primary":
        account.is_primary = False
    assert _discover(service, notice) == ()
    assert session.calls == 0


@pytest.mark.parametrize("second_label", ["Second", "Réseau municipal"])
def test_two_distinct_lender_accounts_are_not_assumed_one_edition_or_one_right(
    harness, second_label,
):
    service, account, first = harness
    second_account = Account(
        identity_id="alex", provider_id="paris",
        credential_id="card-second", unique_id="",
        display_name=second_label, is_primary=True,
    )
    second = ParisOffline(second_account)
    service.accounts.append(second_account)
    service._sessions[service._session_key(
        "paris", second_account.credential_id,
    )] = second
    service.storage.provider_connection_put({
        "identity_id": "alex", "provider_id": "paris",
        "credential_id": "card-second", "label": "Fictive",
        "enabled": True, "metadata": {},
    })
    a = _candidate().notice
    b = _candidate("card-second").notice
    choices = _discover(service, a, b, a)
    assert len(choices) == 2
    assert [choice._notice.source_credential for choice in choices] == [
        "card-alex", "card-second",
    ]
    assert all("sur Paris" in choice.label for choice in choices)
    assert all(not choice.executable and
               choice.supplier_eligibility == "not_verified" for choice in choices)
    assert len({choice.label for choice in choices}) == 2
    assert all("card-alex" not in choice.label and
               "card-second" not in choice.label for choice in choices)
    if second_label == "Réseau municipal":
        assert "choix 1/2" in choices[0].label
        assert "choix 2/2" in choices[1].label
    assert first.calls == second.calls == 0
    assert service.current_identity.identity_id == "default"


def test_epoch_revoked_during_plugin_status_discards_entire_candidate_list(
    harness,
):
    service, _account, session = harness
    original = service._plugin_manager.status

    def status(plugin_id):
        service._account_context_epoch += 1
        return original(plugin_id)

    service._plugin_manager.status = status
    assert _discover(service, _candidate().notice) == ()
    assert session.calls == 0


def test_untrusted_account_does_not_authorize_a_new_borrow(harness):
    service, _account, session = harness
    notice = _candidate().notice
    choices = _discover(service, notice)
    assert len(choices) == 1
    # Discovery is purely a possible lender: no loan borrow checkpoint was
    # written, no right or request was granted, and the next explicit click
    # must revalidate and produce its own durable claim.
    assert choices[0].executable is False
    assert service.storage.loan_borrow_state(
        borrow_claim_key(choices[0]._account, notice),
    ) is None
    assert session.calls == 0


def test_possible_lender_dto_cannot_be_forged_into_an_authorization(harness):
    service, _account, session = harness
    candidate, = _discover(service, _candidate().notice)
    with pytest.raises(ValueError, match="invalid_possible_borrow_lender"):
        replace(candidate, executable=True)
    with pytest.raises(ValueError, match="invalid_possible_borrow_lender"):
        replace(candidate, supplier_eligibility="eligible")
    with pytest.raises(ValueError, match="invalid_possible_borrow_lender"):
        replace(candidate, lender_identity_id="default")
    assert session.calls == 0


def test_shared_credential_in_two_profiles_cannot_select_a_lender(harness):
    service, _account, session = harness
    service.accounts.append(Account(
        identity_id="default", provider_id="paris",
        credential_id="card-alex", unique_id="",
        display_name="Requesting profile", is_primary=True,
    ))
    assert _discover(service, _candidate().notice) == ()
    assert session.calls == 0


def test_explicit_requester_discovery_is_headless_and_rejects_unknown_identity(harness):
    service, _account, session = harness
    class Headless:
        def __getattr__(self, name):
            if name == "current_identity":
                pytest.fail("Core worker read navigation")
            return getattr(service, name)
    proxy = Headless()
    choices = _discover(proxy, _candidate().notice)
    assert len(choices) == 1 and choices[0].lender_identity_id == "alex"
    assert _discover(proxy, _candidate().notice, requester="missing") == ()
    assert session.calls == 0


def test_same_account_two_collections_have_stable_inert_labels(harness):
    service, account, session = harness
    first = _candidate(collection="collection-a").notice
    second = _candidate(collection="collection-b").notice
    a = _discover(service, first, second)
    b = _discover(service, second, first)
    assert len(a) == len(b) == 2
    assert [(c.label, c._notice.source_collection) for c in a] == [
        (c.label, c._notice.source_collection) for c in b
    ]
    assert len({c.label for c in a}) == 2
    assert session.calls == 0


def test_composed_choice_label_cannot_collide_with_literal_profile_label(harness):
    from cifereca.core.borrow_for_identity_discovery import _label_lenders
    service, account, session = harness
    first, = _discover(service, _candidate(collection="collection-a").notice)
    second, = _discover(service, _candidate(collection="collection-b").notice)
    composed = _label_lenders([first, second])
    third = replace(first, label=composed[0].label,
                    _notice=_candidate(collection="collection-c").notice)
    # A different literal label may already contain the generated suffix.
    result = _label_lenders([third, second, first])
    assert len({row.label for row in result}) == 3
    assert all(len(row.label) <= 80 and row.executable is False for row in result)
    assert result[0].label.startswith("Choix 1/3 · ")
    assert all(account.credential_id not in row.label for row in result)
    assert session.calls == 0


@pytest.mark.parametrize("display, expected", [
    ("Bibliothèque locale", "Alex sur Bibliothèque locale"),
    ("https://example.org/account", "Alex sur Compte bibliothèque"),
    ("private@example.org", "Alex sur Compte bibliothèque"),
    ("A" * 80, "Alex sur " + "A" * 32),
])
def test_lender_label_uses_safe_installed_library_descriptor(harness, display, expected):
    service, account, session = harness
    session.descriptor = replace(session.descriptor, display_name=display)
    choice, = _discover(service, _candidate().notice)
    assert choice.label == expected
    assert account.credential_id not in choice.label
    assert choice.executable is False and session.calls == 0


@pytest.mark.parametrize("mutation", [
    {"revision_id": "new-revision"},
    {"scenario": "CATALOGUE"},
    {"source_account_id": ""},
    {"source_account_id": "other-reader"},
])
def test_exact_notice_binding_revoked_without_rotating_claim(harness, mutation):
    from cifereca.core.borrow_for_identity_discovery import possible_lender_matches
    service, account, session = harness
    notice = _candidate().notice
    candidate, = _discover(service, notice)
    key = borrow_claim_key(account, notice)
    assert possible_lender_matches(candidate, notice, requesting_identity_id="default")
    for name, value in mutation.items():
        setattr(notice, name, value)
    assert not possible_lender_matches(candidate, notice, requesting_identity_id="default")
    assert borrow_claim_key(account, notice) == key
    assert session.calls == 0


@pytest.mark.parametrize("changes", [
    {"source_account_id": "foreign"},
    {"source_account_id": False},
    {"revision_id": True},
    {"revision_id": ""},
    {"revision_id": "x" * 1025},
    {"scenario": None},
])
def test_unqualified_extra_scope_never_selects_a_lender(harness, changes):
    service, _account, session = harness
    assert _discover(service, replace(_candidate().notice, **changes)) == ()
    assert session.calls == 0


def test_conflicting_revisions_do_not_pick_first_candidate(harness):
    service, account, session = harness
    first = replace(_candidate().notice, revision_id="one")
    second = replace(first, revision_id="two")
    assert borrow_claim_key(account, first) == borrow_claim_key(account, second)
    assert len(_discover(service, first, replace(first))) == 1
    assert _discover(service, first, second) == ()
    assert _discover(service, second, first) == ()
    other = replace(first, record_id="independent")
    candidate, = _discover(service, first, second, other)
    assert candidate._notice is other
    assert session.calls == 0


@pytest.mark.parametrize("change", [
    "rebind", "disconnect", "generation", "descriptor", "notice", "account",
    "foreign_credential", "duplicate_primary", "duplicate_object", "oversized_inventory",
])
def test_later_source_cannot_publish_obsolete_earlier_lender(harness, change):
    service, account, first = harness
    second_account = Account(
        identity_id="alex", provider_id="paris",
        credential_id="card-second", unique_id="",
        display_name="Second", is_primary=True,
    )
    second = ParisOffline(second_account)
    service.accounts.append(second_account)
    service._sessions[service._session_key("paris", second_account.credential_id)] = second
    service.storage.provider_connection_put({
        "identity_id": "alex", "provider_id": "paris",
        "credential_id": second_account.credential_id, "label": "Fictive",
        "enabled": True, "metadata": {},
    })
    a, b = _candidate().notice, _candidate("card-second").notice
    original = service._plugin_manager.status
    calls = []

    def status(plugin_id):
        calls.append(plugin_id)
        if len(calls) == 2:
            if change == "rebind":
                service._sessions[service._session_key("paris", account.credential_id)] = ParisOffline(account)
            elif change == "disconnect":
                first.connected = False
            elif change == "generation":
                first._epoch += 1
            elif change == "descriptor":
                first.descriptor = replace(first.descriptor, display_name="Changed")
            elif change == "notice":
                a.revision_id = "new-source-revision"
            elif change == "account":
                account.unique_id = "different-borrower"
            elif change == "foreign_credential":
                # The same session key is now registered to the requester.
                service.accounts.append(replace(account, identity_id="default"))
            elif change == "duplicate_primary":
                service.accounts.append(replace(account, display_name="Duplicate"))
            elif change == "duplicate_object":
                service.accounts.append(account)
            else:
                service.accounts.extend([second_account] * 513)
        return original(plugin_id)

    service._plugin_manager.status = status
    assert _discover(service, a, b) == ()
    assert len(calls) == 2
    assert first.calls == second.calls == 0
    assert service.current_identity.identity_id == "default"


@pytest.mark.parametrize("flag, value", [
    ("catalogue_access", "shared_read_only"),
    ("search_provenance", "known_catalog_stale"),
])
def test_readonly_document_occurrence_never_becomes_lender_candidate(harness, flag, value):
    from cifereca.core.borrow_for_identity_discovery import possible_lender_matches
    service, account, session = harness
    notice = _candidate().notice
    native, = _discover(service, notice)
    key = borrow_claim_key(account, notice)
    mirror = replace(notice, metadata={**notice.metadata, flag: value})
    assert _discover(service, mirror) == ()
    notice.metadata = mirror.metadata
    assert not possible_lender_matches(native, notice, requesting_identity_id="default")
    assert _discover(service, notice) == ()
    assert service.storage.loan_borrow_state(key) is None
    assert session.calls == 0


def test_label_composition_cannot_refresh_a_mutated_exact_edition_receipt(
    harness, monkeypatch,
):
    """A callback during 0/1/N labeling must not re-admit new source facts."""
    import cifereca.core.borrow_for_identity_discovery as discovery

    service, _account, session = harness
    first = _candidate(collection="collection-a").notice
    second = _candidate(collection="collection-b").notice
    original_replace = discovery.replace
    relabels = []

    def mutate_during_label(row, **changes):
        if type(row) is PossibleBorrowLender and "label" in changes:
            relabels.append(changes["label"])
            # A valid upstream revision change with the same account/edition
            # happens AFTER the original producer captured its source fact.
            first.revision_id = "newer-source-revision"
        return original_replace(row, **changes)

    monkeypatch.setattr(discovery, "replace", mutate_during_label)
    assert _discover(service, first, second) == ()
    assert relabels
    assert first.revision_id == "newer-source-revision"
    assert session.calls == 0
    assert service.current_identity.identity_id == "default"


def test_label_only_clone_preserves_original_captured_source_contract(harness):
    from cifereca.core.borrow_for_identity_discovery import (
        _relabel_preserving_receipt, possible_lender_matches,
    )

    service, _account, session = harness
    notice = _candidate().notice
    original, = _discover(service, notice)
    renamed = _relabel_preserving_receipt(original, "Alex sur Paris · autre choix")
    assert renamed.label != original.label
    assert renamed._captured_fields is original._captured_fields
    assert renamed._captured_notice_binding is original._captured_notice_binding
    assert possible_lender_matches(
        renamed, notice, requesting_identity_id="default",
    )
    notice.revision_id = "valid-but-new-source-revision"
    assert not possible_lender_matches(
        renamed, notice, requesting_identity_id="default",
    )
    assert session.calls == 0


@pytest.mark.parametrize("change", [
    "disconnect", "closed", "provider_rebind", "credential_rebind",
    "account_unregistered", "duplicate_account",
])
def test_original_lender_session_receipt_rejects_live_revocation_without_epoch(
    harness, change,
):
    """A stale local lender cannot survive a same-epoch session alteration."""
    from cifereca.core.borrow_for_identity_discovery import (
        possible_lender_session_matches,
    )

    service, account, session = harness
    notice = _candidate().notice
    choice, = _discover(service, notice)
    assert possible_lender_session_matches(choice, session)
    original_epoch = session._epoch
    if change == "disconnect":
        session.connected = False
    elif change == "closed":
        session._closed = True
    elif change == "provider_rebind":
        session.provider_id = "other-provider"
    elif change == "credential_rebind":
        session.credential_id = "other-card"
    elif change == "account_unregistered":
        session._accounts = []
    else:
        session._accounts = [account, account]

    assert session._epoch == original_epoch
    assert not possible_lender_session_matches(choice, session)
    assert not possible_lender_session_matches(choice, object())
    assert session.calls == 0
    assert service.current_identity.identity_id == "default"
