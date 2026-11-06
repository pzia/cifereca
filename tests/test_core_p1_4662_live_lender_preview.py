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
    return ObservedBorrowOffer(
        notice=notice, evidence=evidence,
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
    assert option.executable is preview.executable is False
    assert session.calls == 0
    assert service.current_identity.identity_id == "default"
    assert "card-alex" not in repr(preview)


def test_missing_supplier_evidence_is_never_a_borrowable_offer(harness):
    service, account, session = harness
    candidate = _candidate()
    no_evidence = replace(candidate, evidence=None)
    assert _preview(service, no_evidence).status == "none"
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


@pytest.mark.parametrize("mutation", [
    "disconnected", "foreign_owner", "disabled", "wrong_primary",
    "account_missing", "not_primary", "foreign_evidence", "session_rebind",
    "plugin_disabled", "provider_capability_missing", "claim_spent",
])
def test_broken_lender_scope_rejects_without_fallback(harness, mutation):
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
    assert _preview(service, row).options == ()
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
    assert _preview(service, _candidate()).options == ()
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
