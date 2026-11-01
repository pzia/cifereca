"""P0 #2055: canonical manual BnF admission, without a real provider POST.

Fixtures simulate a future BnF with declared borrow capability and explicit
user-site verification. Optional quota/Pass flags are NOT mandatory P0 gates. The production BnF
manifest/method remain disabled, so none of these tests enable borrowing.
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import json

import pytest

from cifereca.core.manual_bnf_borrow import (
    manual_bnf_borrow, manual_bnf_borrow_available,
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
        assert session.reads == []  # no quota/Pass probe is required
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


@pytest.mark.parametrize("site_checked", [False, None, "yes", 1])
def test_visual_site_check_must_be_exact_true_before_any_borrow(
    tmp_path, site_checked,
):
    service, session, account, item = setup(tmp_path)
    try:
        r = manual_bnf_borrow(
            service, item, expected_identity_id="default",
            confirmed=True, site_checked=site_checked,
        )
        assert r["status"] == "blocked"
        assert r["reason_code"] == "borrow_site_check_required"
        assert session.reads == session.dispatches == []
        assert service.storage.loan_borrow_state(
            borrow_claim_key(account, item)) is None
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


@pytest.mark.parametrize("denial", [
    {"permission": "unknown"},
    {"quota_state": "unknown"},
    {"quota_state": "exhausted"},
    {"item_offer": "unavailable"},
    {"account_state": "inactive"},
    {"record_id": "different-book"},
    {"borrower_id": "foreign-account"},
    {"identity_id": "foreign-identity"},
])
def test_optional_p2_markers_do_not_veto_manual_prechecked_p0(tmp_path, denial):
    service, session, account, item = setup(tmp_path)
    try:
        session._evidence = evidence(account, item, **denial)
        r = manual_bnf_borrow(
            service, item, expected_identity_id="default",
            confirmed=True, site_checked=True,
        )
        assert r["status"] == "confirmed"
        assert session.reads == []  # never run optional P2 preflight
        assert session.dispatches == [("book-1", "reader")]
        assert service.storage.loan_borrow_state(
            borrow_claim_key(account, item))["status"] == "confirmed"
    finally:
        service.close()


def test_optional_borrower_evidence_reader_is_never_required(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        session.borrower_eligibility_for = lambda *_: pytest.fail(
            "P2 quota/Pass preflight must not run on P0 manual borrow"
        )
        r = manual_bnf_borrow(
            service, item, expected_identity_id="default",
            confirmed=True, site_checked=True,
        )
        assert r["status"] == "confirmed"
        assert session.reads == []
        assert len(session.dispatches) == 1
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


def test_ui_future_capability_offers_manual_prechecked_action(tmp_path):
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
        assert session.reads == []
        assert service.storage.loan_borrow_state(
            borrow_claim_key(account, item))["status"] == "confirmed"
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
