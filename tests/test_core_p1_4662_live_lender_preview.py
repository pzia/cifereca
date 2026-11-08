"""#4662: live Core/SQLite binding of borrower observations without provider I/O.

No borrowed permissions, supplier POST, ADEPT, copies or hidden account switch.
Every offered choice is a non-executable UI preview.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account, SearchResult
from cifereca.providers.base import ProviderSession, ProviderDescriptor, ProviderCapabilities
from cifereca.plugin_api.lending_capabilities import BorrowerOperationEvidence
from cifereca.plugin_api.lending_borrower_eligibility import (
    BorrowerOperationEvidence as ExactEditionBorrowerEvidence,
)
from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.core.borrow_for_identity_live import ObservedBorrowOffer
from tests.test_sync_state_contract import _service


class ParisOffline(ProviderSession):
    def __init__(self, account):
        super().__init__(
            descriptor=ProviderDescriptor(
                id="paris", display_name="Paris", description="fixture no I/O",
                state="ready", capabilities=ProviderCapabilities(
                    search=True, borrow=True,
                ),
            ),
            credential_id=account.credential_id, label="Fixture",
            credentials={}, options={},
        )
        self._accounts = [account]
        self.connected = True
        self._epoch = 7
        self.calls = 0

    def connect(self):
        self.calls += 1
        raise AssertionError("no connect during preview")

    def close(self):
        self.connected = False

    def search(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("no supplier search during preview")

    def borrow(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("no borrow during preview")

    def borrow_explicit_with_claim(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("no claimed borrow during preview")


def _candidate(credential="card-alex", collection="catalogue"):
    notice = SearchResult(
        id="notice:" + credential, record_id="record-1",
        docbase="DILICOM", medium="digital", title="Fiction",
        author=None, type="ebook", format="EPUB", publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=None, scenario="EBOOKS", source_provider="paris",
        source_credential=credential, source_collection=collection,
    )
    evidence = BorrowerOperationEvidence(
        identity_id="alex", provider_id="paris", credential_id=credential,
        account_id="", branch="DILICOM", operation="loan.borrow",
        subscription="valid", remaining_loans=1, permission="allowed",
    )
    exact = ExactEditionBorrowerEvidence(
        identity_id="alex", provider_id="paris", credential_id=credential,
        borrower_id="", docbase=notice.docbase, record_id=notice.record_id,
        source_collection=notice.source_collection, operation="loan.borrow",
        account_state="active", quota_state="available",
        item_offer="available", permission="allowed",
    )
    return ObservedBorrowOffer(
        notice=notice, evidence=evidence, edition_evidence=exact,
        edition_id="edition-1", observed_at=1000,
    )


@pytest.fixture
def harness(tmp_path):
    service = _service(tmp_path)
    account = Account(
        identity_id="alex", provider_id="paris",
        credential_id="card-alex", unique_id="",
        display_name="Réseau municipal", is_primary=True,
    )
    session = ParisOffline(account)
    service.accounts = [account]
    service._sessions[service._session_key("paris", account.credential_id)] = session
    service._provider_plugin_ids["paris"] = "fixture.paris"
    service._plugin_manager = NS(
        status=lambda _id: NS(
            enabled=True, state="ready",
            descriptor=NS(id="fixture.paris", type="provider.library",
                          capabilities=("acquisition.borrow",)),
        ),
        enabled_for_identity=lambda *_args: True,
    )
    identity = service.config.identity("alex")
    identity["primary_paris_connection_id"] = account.credential_id
    service.config.upsert_identity(identity)
    service.config.upsert_provider_account({
        "id": account.credential_id, "provider": "paris",
        "identity_id": "alex", "enabled": True,
    })
    service.storage.provider_connection_put({
        "identity_id": "alex", "provider_id": "paris",
        "credential_id": account.credential_id,
        "label": "Fictive", "enabled": True, "metadata": {},
    })
    assert service.current_identity.identity_id == "default"
    try:
        yield service, account, session
    finally:
        service.close()


def _preview(service, *rows, now=1100):
    return service.preview_borrow_for_current_identity(
        expected_edition_id="edition-1",
        observations=list(rows), now=now,
    )


def test_alex_exact_observation_is_visible_under_default_but_not_executable(harness):
    service, account, session = harness
    preview = _preview(service, _candidate())
    assert preview.status == "external"
    assert preview.label == "Emprunter avec Alex"
    assert len(preview.options) == 1
    option = preview.options[0]
    assert option.borrowing_identity_id == "alex"
    assert option.transfer_state == "unknown"
    assert preview.rejection_reasons == ()
    assert option.executable is preview.executable is False
    assert session.calls == 0
    assert service.current_identity.identity_id == "default"
    assert "card-alex" not in repr(preview)


def test_missing_supplier_evidence_is_never_a_borrowable_offer(harness):
    service, account, session = harness
    candidate = _candidate()
    no_evidence = replace(candidate, evidence=None)
    preview = _preview(service, no_evidence)
    assert preview.status == "none"
    assert preview.rejection_reasons == ("permission_unverified",)
    assert _preview(service, replace(
        candidate, evidence=replace(candidate.evidence, permission="unknown"),
    )).options == ()
    assert _preview(service, replace(
        candidate, evidence=replace(candidate.evidence, remaining_loans=0),
    )).options == ()
    assert _preview(service, replace(
        candidate, evidence=replace(candidate.evidence, subscription="renewal_pending"),
    )).options == ()
    assert session.calls == 0


@pytest.mark.parametrize(("mutation", "reason"), [
    ("disconnected", "lender_unavailable"),
    ("foreign_owner", "lender_unavailable"),
    ("disabled", "lender_unavailable"),
    ("wrong_primary", "lender_unavailable"),
    ("account_missing", "lender_unavailable"),
    ("not_primary", "lender_unavailable"),
    ("foreign_evidence", "lender_unavailable"),
    ("session_rebind", "lender_unavailable"),
    ("plugin_disabled", "capability_unverified"),
    ("provider_capability_missing", "capability_unverified"),
    ("claim_spent", "previous_attempt"),
])
def test_broken_lender_scope_rejects_without_fallback(harness, mutation, reason):
    service, account, session = harness
    row = _candidate()
    if mutation == "disconnected":
        session.connected = False
    elif mutation == "foreign_owner":
        service.storage.provider_connection_put({
            "identity_id": "default", "provider_id": "paris",
            "credential_id": account.credential_id,
            "label": "Wrong", "enabled": True, "metadata": {},
        })
    elif mutation == "disabled":
        service.storage.provider_connection_put({
            "identity_id": "alex", "provider_id": "paris",
            "credential_id": account.credential_id,
            "label": "Fictive", "enabled": False, "metadata": {},
        })
    elif mutation == "wrong_primary":
        saved = service.config.identity("alex")
        saved["primary_paris_connection_id"] = "another"
        service.config.upsert_identity(saved)
    elif mutation == "account_missing":
        service.accounts = []
    elif mutation == "not_primary":
        account.is_primary = False
    elif mutation == "foreign_evidence":
        row = replace(row, evidence=replace(row.evidence, identity_id="default"))
    elif mutation == "session_rebind":
        service._sessions[service._session_key("paris", "card-alex")] = object()
    elif mutation == "plugin_disabled":
        service._plugin_manager.status = lambda _id: NS(
            enabled=False, state="ready",
            descriptor=NS(id="fixture.paris", type="provider.library",
                          capabilities=("acquisition.borrow",)),
        )
    elif mutation == "provider_capability_missing":
        service._plugin_manager.status = lambda _id: NS(
            enabled=True, state="ready",
            descriptor=NS(id="fixture.paris", type="provider.library",
                          capabilities=()),
        )
    else:
        service.storage.metadata_set(
            borrow_claim_key(account, row.notice), "spent-claim",
        )
    preview = _preview(service, row)
    assert preview.options == ()
    assert preview.rejected_count == 1
    assert preview.rejection_reasons == (reason,)
    assert session.calls == 0


def test_same_lender_separate_collections_retain_two_explicit_choices(harness):
    service, account, session = harness
    first = _candidate(collection="collection-1")
    second = _candidate(collection="collection-2")
    a = _preview(service, second, first)
    b = _preview(service, first, second)
    assert a == b
    assert a.status == "choose"
    assert a.requires_explicit_choice
    assert len({o.option_id for o in a.options}) == 2
    assert len({o.label for o in a.options}) == 2
    assert all(not o.executable for o in a.options)
    assert session.calls == 0


def test_context_aba_and_source_epoch_revoke_old_option(harness):
    service, account, session = harness
    first = _preview(service, _candidate()).options[0]
    service._account_context_epoch += 2
    again = _preview(service, _candidate()).options[0]
    assert first.option_id != again.option_id
    session._epoch += 1
    third = _preview(service, _candidate()).options[0]
    assert third.option_id != again.option_id
    assert session.calls == 0


def test_rebind_during_plugin_inspection_returns_no_actions(harness):
    service, account, session = harness
    old_status = service._plugin_manager.status

    def rebind(plugin_id):
        service._account_context_epoch += 1
        return old_status(plugin_id)

    service._plugin_manager.status = rebind
    preview = _preview(service, _candidate())
    assert preview.options == ()
    assert preview.rejection_reasons == ("context_changed",)
    assert session.calls == 0


def test_source_replacement_during_plugin_inspection_returns_no_actions(harness):
    service, account, session = harness
    old_status = service._plugin_manager.status

    def revoke(plugin_id):
        service._source_context_epoch += 1
        return old_status(plugin_id)

    service._plugin_manager.status = revoke
    assert _preview(service, _candidate()).options == ()
    assert session.calls == 0


def test_unsafe_lender_display_name_never_leaks_into_action(harness):
    service, account, session = harness
    service.identities = [
        replace(identity, label="alex@example.com")
        if identity.identity_id == "alex" else identity
        for identity in service.identities
    ]
    output = _preview(service, _candidate())
    assert output.status == "external"
    assert output.label == "Emprunter avec Profil prêteur"
    assert "example.com" not in repr(output)


def test_invalid_input_is_rejected_before_any_provider_access(harness):
    service, account, session = harness
    for invalid in (None, {}, [object()], [_candidate()] * 65):
        with pytest.raises(ValueError, match="invalid_cross_profile_borrow_observations"):
            service.preview_borrow_for_current_identity(
                expected_edition_id="edition-1", observations=invalid, now=1100,
            )
    assert session.calls == 0


def _click(service, captured, selected_option_id, *rows):
    return service.revalidate_borrow_lender_choice(
        captured_preview=captured, selected_option_id=selected_option_id,
        expected_edition_id="edition-1", observations=list(rows), now=1100,
    )


def test_explicit_current_lender_click_is_only_an_inert_preflight(harness):
    service, account, session = harness
    row = _candidate()
    preview = _preview(service, row)
    selection = _click(service, preview, preview.options[0].option_id, row)
    assert selection.status == "current"
    assert selection.reason_code == "selection_current_unverified"
    assert selection.borrowing_identity_id == "alex"
    assert selection.label == "Emprunter avec Alex"
    assert selection.transfer_state == "unknown"
    assert selection.executable is False
    assert "card-alex" not in repr(selection)
    assert session.calls == 0
    assert service.current_identity.identity_id == "default"


def test_no_option_and_foreign_or_malformed_clicked_id_fails_closed(harness):
    service, account, session = harness
    row = _candidate()
    preview = _preview(service, row)
    for selected in (None, "", "other", "borrow-profile.v1:unknown"):
        selected_result = _click(service, preview, selected, row)
        assert selected_result.status == "revoked"
        assert selected_result.executable is False
    empty = _preview(service)
    assert _click(service, empty, preview.options[0].option_id, row).status == "revoked"
    assert session.calls == 0


def test_two_collections_need_exact_clicked_choice_no_first_fallback(harness):
    service, account, session = harness
    a = _candidate(collection="catalogue-A")
    b = _candidate(collection="catalogue-B")
    preview = _preview(service, a, b)
    assert preview.status == "choose"
    one, two = preview.options
    for option in preview.options:
        resolved = _click(service, preview, option.option_id, b, a)
        assert resolved.status == "current"
        assert resolved.label == option.label
    assert _click(service, preview, one.option_id, b).status == "revoked"
    assert _click(service, preview, two.option_id, a).status == "revoked"
    assert session.calls == 0


def test_lender_selection_aba_and_source_epoch_change_are_revoked(harness):
    service, account, session = harness
    row = _candidate()
    original = _preview(service, row)
    old = original.options[0].option_id
    service._account_context_epoch += 2
    assert _click(service, original, old, row).status == "revoked"
    renewed = _preview(service, row)
    assert _click(service, renewed, renewed.options[0].option_id, row).status == "current"
    session._epoch += 1
    assert _click(service, renewed, renewed.options[0].option_id, row).status == "revoked"
    assert session.calls == 0


def test_lender_choice_refuses_claim_and_disconnection_without_retry(harness):
    service, account, session = harness
    row = _candidate()
    preview = _preview(service, row)
    selected = preview.options[0].option_id
    service.storage.metadata_set(borrow_claim_key(account, row.notice), "spent")
    assert _click(service, preview, selected, row).status == "revoked"
    assert session.calls == 0


def test_lender_selection_change_of_profile_refuses_old_callback(harness):
    service, account, session = harness
    row = _candidate()
    preview = _preview(service, row)
    service.current_identity = service.identity_by_id("alex")
    service._account_context_epoch += 1
    stale = _click(service, preview, preview.options[0].option_id, row)
    assert stale.status == "revoked"
    assert stale.executable is False
    assert session.calls == 0


def test_transfer_is_never_silently_promoted_to_a_copy_permission(harness):
    service, account, session = harness
    row = replace(_candidate(), transfer_state="blocked")
    preview = _preview(service, row)
    selected = _click(service, preview, preview.options[0].option_id, row)
    assert selected.status == "current"
    assert selected.transfer_state == "blocked"
    assert selected.executable is False
    # An observed lender permission cannot qualify transfer/use of a copy.
    assert _click(service, preview, preview.options[0].option_id,
                  replace(row, transfer_state="unknown")).status == "revoked"
    assert session.calls == 0


def test_expired_or_missing_observation_revokes_held_selection(harness):
    service, account, session = harness
    row = _candidate()
    preview = _preview(service, row)
    selected = preview.options[0].option_id
    assert _click(service, preview, selected,
                  replace(row, observed_at=1)).status == "revoked"
    assert _click(service, preview, selected).status == "revoked"
    assert session.calls == 0


def test_global_source_rebind_revokes_click_even_when_session_epoch_repeats(harness):
    service, account, old_session = harness
    row = _candidate()
    original = _preview(service, row)
    old_token = original.options[0].option_id

    # A physically new provider session may start at the same local _epoch.
    replacement = ParisOffline(account)
    assert replacement._epoch == old_session._epoch
    service._sessions[service._session_key("paris", "card-alex")] = replacement
    service._source_context_epoch += 1

    fresh = _preview(service, row)
    assert fresh.status == "external"
    assert fresh.options[0].option_id != old_token
    assert _click(service, original, old_token, row).status == "revoked"
    assert _click(service, fresh, fresh.options[0].option_id, row).status == "current"
    assert old_session.calls == replacement.calls == 0


def test_account_wide_positive_without_exact_edition_attestation_is_not_a_choice(harness):
    service, account, session = harness
    base = _candidate()
    # This account's subscription/quota are positive. The supplier has not
    # attested any offer for the actual book: do NOT extrapolate.
    assert _preview(service, replace(base, edition_evidence=None)).status == "none"
    assert _preview(service, replace(base, edition_evidence=None)).options == ()
    assert session.calls == 0


@pytest.mark.parametrize("field,value", [
    ("identity_id", "default"),
    ("provider_id", "other"),
    ("credential_id", "card-different"),
    ("borrower_id", "other-borrower"),
    ("docbase", "SYRACUSE"),
    ("record_id", "another-digital-edition"),
    ("source_collection", "other-collection"),
    ("operation", "loan.return"),
    ("account_state", "renewal_pending"),
    ("quota_state", "exhausted"),
    ("item_offer", "unavailable"),
    ("permission", "denied"),
    ("account_state", "unknown"),
    ("quota_state", "unknown"),
    ("item_offer", "unknown"),
    ("permission", "unknown"),
])
def test_foreign_denied_or_incomplete_exact_book_evidence_cannot_unlock_action(
    harness, field, value,
):
    service, account, session = harness
    base = _candidate()
    changed = replace(base, edition_evidence=replace(
        base.edition_evidence, **{field: value},
    ))
    assert _preview(service, changed).status == "none"
    assert session.calls == 0


def test_same_title_different_record_rejects_other_editions_attestation(harness):
    service, account, session = harness
    base = _candidate()
    foreign_edition = replace(
        base, notice=replace(
            base.notice, record_id="edition-2-same-title", title="Fiction",
        ),
    )
    assert _preview(service, foreign_edition).options == ()
    assert _preview(service, base).status == "external"
    assert session.calls == 0


def test_old_selected_edition_permission_revoked_when_evidence_changes(harness):
    service, account, session = harness
    base = _candidate()
    first = _preview(service, base)
    assert len(first.options) == 1
    selected = first.options[0].option_id
    new_offer = replace(
        base, edition_evidence=replace(
            base.edition_evidence, item_offer="unknown",
        ),
    )
    denied = _click(service, first, selected, new_offer)
    assert denied.status == "revoked"
    assert denied.executable is False
    assert session.calls == 0


def test_other_collection_exact_permission_not_reused_by_another_route(harness):
    service, account, session = harness
    base = _candidate(collection="collection-1")
    foreign_collection = replace(
        base, notice=replace(base.notice, source_collection="collection-2"),
    )
    assert _preview(service, foreign_collection).options == ()
    assert _preview(service, base).status == "external"
    assert session.calls == 0


@pytest.mark.parametrize("change", [
    {"edition_evidence": None},
    {"edition_evidence": "denied"},
    {"edition_evidence": "unknown"},
    {"evidence": "quota_reached"},
    {"transfer_state": "blocked"},
])
def test_conflicting_same_edition_observation_blocks_old_positive_in_any_order(
    harness, change,
):
    service, account, session = harness
    positive = _candidate()
    if change.get("edition_evidence") == "denied":
        other = replace(positive, edition_evidence=replace(
            positive.edition_evidence, item_offer="unavailable",
        ))
    elif change.get("edition_evidence") == "unknown":
        other = replace(positive, edition_evidence=replace(
            positive.edition_evidence, item_offer="unknown",
        ))
    elif change.get("edition_evidence") is None:
        other = replace(positive, edition_evidence=None)
    elif change.get("evidence") == "quota_reached":
        other = replace(positive, evidence=replace(
            positive.evidence, remaining_loans=0,
        ))
    else:
        other = replace(positive, transfer_state="blocked")
    for rows in ((positive, other), (other, positive)):
        preview = _preview(service, *rows)
        assert preview.status == "none"
        assert preview.rejected_count == 1
        assert preview.rejection_reasons == ("conflicting_observations",)
    assert session.calls == 0


def test_identical_duplicates_can_be_collapsed_without_lender_duplication(harness):
    service, account, session = harness
    original = _candidate()
    # Two independent notice rows can refer to the exact same offering.
    same_offer = replace(
        original, notice=replace(original.notice, id="other-search-row"),
    )
    preview = _preview(service, original, same_offer)
    assert preview.status == "external"
    assert len(preview.options) == 1
    assert preview.options[0].executable is False
    assert session.calls == 0


def test_two_different_freshness_snapshots_on_same_route_are_ambiguous(harness):
    service, account, session = harness
    base = _candidate()
    changed = replace(base, observed_at=1001)
    assert _preview(service, base, changed).options == ()
    assert _preview(service, changed, base).options == ()
    assert session.calls == 0


def test_denial_on_other_collection_does_not_veto_qualified_lender(harness):
    service, account, session = harness
    first = _candidate(collection="collection-1")
    second = _candidate(collection="collection-2")
    denied_second = replace(second, edition_evidence=replace(
        second.edition_evidence, item_offer="unavailable",
    ))
    actual = _preview(service, first, denied_second)
    assert actual.status == "external"
    assert len(actual.options) == 1
    # An independent source route (even for the same borrower) remains valid.
    assert actual.options[0]._source_coordinates[6] == "collection-1"
    assert session.calls == 0


def test_unrelated_borrower_negative_cannot_veto_matching_borrower(harness):
    service, account, session = harness
    positive = _candidate()
    other_borrower = replace(positive, evidence=replace(
        positive.evidence, identity_id="default", account_id="other",
    ))
    assert _preview(service, positive, other_borrower).status == "external"
    assert session.calls == 0


def test_new_explicit_denial_rejects_held_click_even_if_old_positive_remains(harness):
    service, account, session = harness
    positive = _candidate()
    before = _preview(service, positive)
    assert before.status == "external"
    now_denied = replace(positive, edition_evidence=replace(
        positive.edition_evidence, item_offer="unavailable",
    ))
    stale = _click(
        service, before, before.options[0].option_id,
        positive, now_denied,
    )
    assert stale.status == "revoked"
    assert stale.reason_code == "selection_stale"
    assert stale.executable is False
    assert session.calls == 0


def test_exact_negative_without_account_wide_report_still_revokes_positive(harness):
    service, account, session = harness
    positive = _candidate()
    # A supplier can attest a specific book is unavailable without knowing
    # subscription/quota. That negative must still defeat an old positive.
    negative = replace(
        positive, evidence=None,
        edition_evidence=replace(
            positive.edition_evidence,
            account_state="unknown", quota_state="unknown",
            item_offer="unavailable", permission="unknown",
        ),
    )
    for ordered in ((positive, negative), (negative, positive)):
        assert _preview(service, *ordered).options == ()
    assert session.calls == 0


def test_negative_for_other_borrower_or_book_cannot_veto_positive(harness):
    service, account, session = harness
    positive = _candidate()
    foreign_borrower = replace(
        positive, evidence=None, edition_evidence=replace(
            positive.edition_evidence,
            borrower_id="another-reader", item_offer="unavailable",
        ),
    )
    foreign_edition = replace(
        positive, evidence=None, edition_evidence=replace(
            positive.edition_evidence,
            record_id="other-edition", item_offer="unavailable",
        ),
    )
    assert _preview(service, positive, foreign_borrower).status == "external"
    assert _preview(service, positive, foreign_edition).status == "external"
    assert session.calls == 0


def test_held_lender_choice_revoked_after_positive_fact_refresh(harness):
    service, account, session = harness
    initial = _candidate()
    preview = _preview(service, initial)
    assert len(preview.options) == 1
    old_token = preview.options[0].option_id
    refreshed = replace(initial, observed_at=1001)
    next_preview = _preview(service, refreshed)
    assert next_preview.status == preview.status == "external"
    assert next_preview.options[0]._source_coordinates == preview.options[0]._source_coordinates
    assert next_preview.options[0].option_id != old_token
    assert _click(service, preview, old_token, refreshed).status == "revoked"
    assert _click(service, next_preview, next_preview.options[0].option_id,
                  refreshed).status == "current"
    assert session.calls == 0


@pytest.mark.parametrize("enabled", [False, True])
def test_duplicate_lender_binding_never_qualifies_preview_even_when_enabled(
    harness, monkeypatch, enabled,
):
    service, account, session = harness
    real = service.storage.provider_connections
    candidate = _candidate()
    rows = real(identity_id="alex")
    exact = next(
        row for row in rows
        if row.get("credential_id") == account.credential_id
    )
    # Even an enabled+disabled pair is ambiguous; an enabled+enabled
    # pair is ambiguous too. No first-enabled or last-enabled fallback.
    def duplicate(*, identity_id):
        result = real(identity_id=identity_id)
        if identity_id == "alex":
            return [*result, {**exact, "enabled": enabled}]
        return result

    monkeypatch.setattr(service.storage, "provider_connections", duplicate)
    snapshot = _preview(service, candidate)
    assert snapshot.status == "none"
    assert snapshot.options == ()
    assert snapshot.rejection_reasons == ("lender_unavailable",)
    assert session.calls == 0


def test_held_lender_choice_revoked_when_binding_becomes_ambiguous(
    harness, monkeypatch,
):
    service, account, session = harness
    candidate = _candidate()
    old = _preview(service, candidate)
    selected = old.options[0].option_id
    original = service.storage.provider_connections

    def duplicate(*, identity_id):
        rows = original(identity_id=identity_id)
        if identity_id == "alex":
            exact = next(x for x in rows
                         if x.get("credential_id") == account.credential_id)
            return [*rows, {**exact, "enabled": False}]
        return rows

    monkeypatch.setattr(service.storage, "provider_connections", duplicate)
    result = _click(service, old, selected, candidate)
    assert result.status == "revoked"
    assert result.reason_code == "selection_stale"
    assert result.executable is False
    assert session.calls == 0


@pytest.mark.parametrize("opaque_epoch", [
    "session-uuid-opaque-0123456789abcdef", "a" * 128,
])
def test_opaque_provider_session_epoch_is_scoped_without_provider_io(
    harness, opaque_epoch,
):
    service, account, session = harness
    session._epoch = opaque_epoch
    row = _candidate()
    original = _preview(service, row)
    assert original.status == "external"
    assert len(original.options) == 1
    assert opaque_epoch not in original.options[0].option_id
    selected = original.options[0].option_id
    assert _click(service, original, selected, row).status == "current"
    session._epoch = "new-session-uuid"
    assert _click(service, original, selected, row).status == "revoked"
    assert _preview(service, row).options[0].option_id != selected
    assert session.calls == 0


@pytest.mark.parametrize("invalid", ["", "a" * 129, "bad\\nsecret", -1, True, None])
def test_malformed_provider_session_epoch_cannot_create_lender_offer(
    harness, invalid,
):
    service, account, session = harness
    session._epoch = invalid
    result = _preview(service, _candidate())
    assert result.status == "none"
    assert result.options == ()
    assert result.rejection_reasons == ("scope_unverified",)
    assert session.calls == 0
