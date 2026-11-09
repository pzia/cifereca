"""P0 #2055: canonical manual BnF admission, without a real provider POST.

Synthetic Core contracts exercise explicit user intent without a site-check popup,
with durable at-most-once and no real network or lender interaction.
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import json

import pytest

from functools import partial
from cifereca.core.manual_library_borrow import (
    manual_borrow, manual_borrow_available,
)

# The provider-specific wrapper is retired; preserve the exact test
# preconditions through the generic Core admission contract.
manual_bnf_borrow = partial(
    manual_borrow, provider_id="bnf-pret", docbase="BNF_PRET",
)
manual_bnf_borrow_available = partial(
    manual_borrow_available, provider_id="bnf-pret", docbase="BNF_PRET",
)
from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.models import Account, Loan, SearchResult
from cifereca.providers.base import (
    ProviderCapabilities, ProviderDescriptor, ProviderSession,
)
from cifereca.plugin_api import BorrowerOperationEvidence
from tests.test_sync_state_contract import _service


def notice(a):
    return SearchResult(
        id="bnf-pret:book-1", record_id="book-1",
        docbase="BNF_PRET", medium="digital", title="Synthetic book",
        author=None, type="Livre numérique", format=None,
        publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="BNF_PRET",
        source_provider="bnf-pret", source_credential=a.credential_id,
    )


def evidence(a, item, **overrides):
    values = dict(
        identity_id=a.identity_id, provider_id=a.provider_id,
        credential_id=a.credential_id, borrower_id=a.unique_id,
        docbase=item.docbase, record_id=item.record_id,
        source_collection=item.source_collection,
        account_state="active", quota_state="available",
        item_offer="available", permission="allowed",
    )
    values.update(overrides)
    return BorrowerOperationEvidence(**values)


class FutureBnf(ProviderSession):
    def __init__(self, account):
        descriptor = ProviderDescriptor(
            id="bnf-pret", display_name="Synthetic BnF future",
            description="no network", state="ready",
            capabilities=ProviderCapabilities(
                loans=True, search=True, holdings=True, borrow=True),
        )
        super().__init__(
            descriptor=descriptor, credential_id=account.credential_id,
            label="Synthetic", credentials={}, options={},
        )
        self.connected = True
        self._closed = False
        self._epoch = "fixed-epoch"
        self._accounts = [account]
        self.reads = []
        self.dispatches = []
        self._evidence = None
        self.on_read = lambda: None
        self.on_execute = lambda: None
        self.failure = None

    def connect(self):
        raise AssertionError("no authentication is expected")

    def close(self):
        self.connected = False

    def borrow(self, item, account):
        raise AssertionError("base ProviderSession.borrow MUST NOT be called")

    def borrower_eligibility_for(self, item, account):
        self.reads.append((item.record_id, account.unique_id))
        self.on_read()
        return self._evidence if self._evidence is not None else evidence(account, item)

    def borrow_explicit_with_claim(self, item, account, *, admission,
                                   claimed_receipt, confirmed,
                                   core_scope_current):
        assert confirmed is True and admission.admitted is True
        assert claimed_receipt["status"] == "request_sent"
        assert core_scope_current() is True
        self.dispatches.append((item.record_id, account.unique_id))
        self.on_execute()
        if self.failure is not None:
            raise self.failure
        return Loan(
            id="loan-synthetic", record_id=item.record_id,
            title=item.title, provider="BNF_PRET", medium="digital",
            state="active", source_provider="bnf-pret",
            source_credential=account.credential_id,
            source_account_id=account.unique_id,
            identity_id=account.identity_id,
        )


def setup(tmp_path, *, declared=True):
    service = _service(tmp_path)
    account = Account(
        identity_id="default", provider_id="bnf-pret",
        credential_id="credential", unique_id="reader", display_name="Synthetic",
    )
    item = notice(account)
    session = FutureBnf(account)
    service.accounts = [account]
    service._sessions[service._session_key("bnf-pret", account.credential_id)] = session
    service.storage.provider_connection_put(dict(
        identity_id="default", provider_id="bnf-pret", credential_id="credential",
        label="Synthetic", enabled=True, metadata={},
    ))
    service._provider_plugin_ids["bnf-pret"] = "provider.bnf-pret"
    caps = ("acquisition.borrow", "sync.loans") if declared else ("sync.loans",)
    service._plugin_manager = NS(
        status=lambda _id: NS(
            state="ready", enabled=True,
            descriptor=NS(id="provider.bnf-pret", type="provider.library",
                          capabilities=caps),
        )
    )
    return service, session, account, item


def test_success_requires_real_admission_claim_and_positive_result(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        result = manual_bnf_borrow(
            service, item, expected_identity_id="default", confirmed=True, site_checked=True)
        assert result["status"] == "confirmed" and result["provider_verified"] is True
        assert session.reads == []
        assert session.dispatches == [("book-1", "reader")]
        row = service.storage.loan_borrow_state(borrow_claim_key(account, item))
        assert row["status"] == "confirmed"
        assert row["verified_loan_id"] == "loan-synthetic"
        assert "Synthetic" not in json.dumps(result)
        repeat = manual_bnf_borrow(
            service, item, expected_identity_id="default", confirmed=True, site_checked=True)
        assert repeat["status"] == "verification_unavailable"
        assert len(session.dispatches) == 1 and session.reads == []
        assert service.storage.entitlements(provider_id="bnf-pret") == []
        assert not service.storage.acquisitions()
    finally:
        service.close()


@pytest.mark.parametrize("confirmed", [False, None, "yes", 1])
def test_consent_must_be_exact_true_before_any_source_read_or_claim(tmp_path, confirmed):
    service, session, account, item = setup(tmp_path)
    try:
        r = manual_bnf_borrow(
            service, item, expected_identity_id="default", confirmed=confirmed)
        assert r["status"] == "blocked"
        assert session.reads == session.dispatches == []
        assert service.storage.loan_borrow_state(borrow_claim_key(account, item)) is None
    finally:
        service.close()


def test_real_bnf_has_unpublished_borrow_capability_and_cannot_dispatch(tmp_path):
    service, session, account, item = setup(tmp_path, declared=False)
    try:
        r = manual_bnf_borrow(
            service, item, expected_identity_id="default", confirmed=True, site_checked=True)
        assert r["status"] == "unsupported"
        assert session.reads == session.dispatches == []
        assert service.storage.loan_borrow_state(borrow_claim_key(account, item)) is None
    finally:
        service.close()


@pytest.mark.parametrize("unverified", [
    {"permission": "unknown"},
    {"quota_state": "unknown"},
    {"account_state": "unknown"},
    {"item_offer": "unknown"},
    {"quota_state": "exhausted"},
    {"account_state": "inactive"},
    {"record_id": "different-book"},
])
def test_automated_quota_and_entitlement_markers_do_not_gate_manual_attempt(
        tmp_path, unverified):
    service, session, account, item = setup(tmp_path)
    try:
        # An optional informational evidence snapshot is not read by the
        # Core's manual BnF path. The actual BnF server can refuse the POST.
        session._evidence = evidence(account, item, **unverified)
        r = manual_bnf_borrow(
            service, item, expected_identity_id="default",
            confirmed=True, site_checked=True)
        assert r["status"] == "confirmed"
        assert session.reads == []
        assert session.dispatches == [("book-1", "reader")]
    finally:
        service.close()


@pytest.mark.parametrize("checked", [False, None, "yes", 1])
def test_retired_site_check_keyword_cannot_veto_a_confirmed_manual_request(
    tmp_path, checked,
):
    service, session, account, item = setup(tmp_path)
    try:
        # Legacy callers may supply this obsolete keyword. It has no
        # authorization or website-attestation meaning after #3121.
        r = manual_bnf_borrow(
            service, item, expected_identity_id="default",
            confirmed=True, site_checked=checked)
        assert r["status"] == "confirmed"
        assert r["provider_verified"] is True
        assert session.reads == []
        assert session.dispatches == [("book-1", "reader")]
        assert service.storage.loan_borrow_state(
            borrow_claim_key(account, item))["status"] == "confirmed"
    finally:
        service.close()


def test_non_primary_account_cannot_issue_manual_loan(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        account.is_primary = False
        r = manual_bnf_borrow(
            service, item, expected_identity_id="default",
            confirmed=True, site_checked=True)
        assert r["status"] == "blocked"
        assert session.dispatches == []
    finally:
        service.close()


def test_timeout_or_unknown_post_state_is_not_replayed(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        session.failure = TimeoutError("PRIVATE-URL-synthetic")
        result = manual_bnf_borrow(
            service, item, expected_identity_id="default", confirmed=True, site_checked=True)
        assert result["status"] == "verification_unavailable"
        assert "PRIVATE" not in json.dumps(result)
        assert len(session.dispatches) == 1
        assert service.storage.loan_borrow_state(
            borrow_claim_key(account, item))["status"] == "verification_unavailable"
        result = manual_bnf_borrow(
            service, item, expected_identity_id="default", confirmed=True, site_checked=True)
        assert result["status"] == "verification_unavailable"
        assert len(session.dispatches) == 1 and session.reads == []
    finally:
        service.close()


def test_scope_revocation_during_post_cannot_publish_right_or_confirm(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        session.on_execute = lambda: setattr(session, "_epoch", "new-epoch")
        result = manual_bnf_borrow(
            service, item, expected_identity_id="default", confirmed=True, site_checked=True)
        assert result["status"] == "verification_unavailable"
        assert len(session.dispatches) == 1
        assert not service.storage.acquisitions()
        assert not service.storage.entitlements(provider_id="bnf-pret")
    finally:
        service.close()


def test_wrong_source_or_identity_never_touches_account_or_claim(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        for candidate, identity in [
            (replace(item, source_provider="paris"), "default"),
            (replace(item, source_credential="other"), "default"),
            (replace(item, record_id=""), "default"),
            (item, "foreign"),
        ]:
            assert manual_bnf_borrow(
                service, candidate, expected_identity_id=identity,
                confirmed=True, site_checked=True)["status"] == "blocked"
        assert session.reads == session.dispatches == []
    finally:
        service.close()


def test_legacy_gtk_borrow_never_dispatches_bnf_even_if_capability_changes():
    """An accidental manifest promotion cannot route BnF to session.borrow."""
    import ast
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    main = next(n for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    for name in ("can_borrow_item", "borrow_search_item"):
        method = next(n for n in main.body
                      if isinstance(n, ast.FunctionDef) and n.name == name)
        guard = method.body[0]
        assert isinstance(guard, ast.If)
        assert ast.unparse(guard.test) == (
            "getattr(item, 'source_provider', None) == 'bnf-pret'"
        )
        assert isinstance(guard.body[-1], ast.Return)
        if name == "can_borrow_item":
            assert ast.unparse(guard.body[-1].value) == (
                "self.service.can_borrow_bnf(item)"
            )
        else:
            assert guard.body[-1].value is None
            assert any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "present_bnf_borrow"
                for call in ast.walk(guard)
            )
    # Generic session.borrow remains only in the non-BnF branch.
    source = ast.unparse(next(n for n in main.body
                             if isinstance(n, ast.FunctionDef)
                             and n.name == "borrow_search_item"))
    assert source.index("present_bnf_borrow(") < source.index("session.borrow(")


def test_inert_ui_visibility_ignores_catalogue_button_without_capability(tmp_path):
    service, session, account, item = setup(tmp_path, declared=False)
    try:
        assert manual_bnf_borrow_available(service, item) is False
        assert service.can_borrow_bnf(item) is False
        assert session.reads == session.dispatches == []
        assert not service.storage.acquisitions()
    finally:
        service.close()


def test_ui_future_capability_only_offers_action_not_permission(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        assert manual_bnf_borrow_available(service, item) is True
        assert service.can_borrow_bnf(item) is True
        session._evidence = evidence(account, item, permission="unknown")
        assert manual_bnf_borrow_available(service, item) is True
        assert session.reads == session.dispatches == []
        result = service.borrow_bnf_manual(
            item, expected_identity_id="default", confirmed=True, site_checked=True,
            expected_context_epoch=service._account_context_epoch,
            expected_account=account,
        )
        assert result["status"] == "confirmed"
        assert session.dispatches == [("book-1", "reader")]
        assert service.storage.loan_borrow_state(
            borrow_claim_key(account, item))["status"] == "confirmed"
    finally:
        service.close()


@pytest.mark.parametrize("raw", [
    "corrupt-before-restart",
    "",
    '{"schema":1,"operation":"loan.borrow","status":"unknown"}',
])
def test_ui_hides_button_on_corrupt_spent_borrow_checkpoint(tmp_path, raw):
    """The storage rejects re-POST on *presence* even if decoding fails."""
    service, session, account, item = setup(tmp_path)
    try:
        key = borrow_claim_key(account, item)
        assert key
        assert manual_bnf_borrow_available(service, item) is True
        service.storage.metadata_set(key, raw)
        assert service.storage.metadata_get(key) is not None
        assert service.storage.loan_borrow_state(key) is None
        assert service.storage.loan_borrow_claim_once(key, "retry")[0] is False
        assert manual_bnf_borrow_available(service, item) is False
        assert service.can_borrow_bnf(item) is False
        outcome = service.borrow_bnf_manual(
            item, expected_identity_id="default",
            confirmed=True, site_checked=True,
            expected_context_epoch=service._account_context_epoch,
            expected_account=account,
        )
        assert outcome["status"] == "verification_unavailable"
        assert outcome["reason_code"] == "borrow_previous_attempt"
        assert session.reads == session.dispatches == []
    finally:
        service.close()


def test_ui_consent_tied_to_exact_account_ref_and_core_epoch(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        prior = service._account_context_epoch
        for kw in (
            {"expected_context_epoch": prior + 1},
            {"expected_context_epoch": "0"},
            {"expected_account": replace(account)},
        ):
            result = service.borrow_bnf_manual(
                item, expected_identity_id="default", confirmed=True, site_checked=True, **kw)
            assert result["status"] == "blocked"
        assert session.reads == session.dispatches == []
        assert service.storage.loan_borrow_state(
            borrow_claim_key(account, item)) is None
        # A -> B -> A must not replay consent even when the same account
        # instance and selected source have been restored.
        service._account_context_epoch += 2
        result = service.borrow_bnf_manual(
            item, expected_identity_id="default", confirmed=True, site_checked=True,
            expected_context_epoch=prior, expected_account=account)
        assert result["status"] == "blocked"
        assert session.dispatches == []
    finally:
        service.close()
