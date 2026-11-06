"""#3118: real Core/SQLite + Paris adapter with entirely synthetic HTTP.

The mutating ASSARedirect GET is checked at the transport boundary, including
claim persistence after restart. No request reaches an actual lender.
"""
import ast
from dataclasses import replace
import json
from pathlib import Path
import sys
import sqlite3
from types import SimpleNamespace as NS
from urllib.parse import quote

import httpx
import pytest

from cifereca.brokers import IdentityBrokerImpl
from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.gui.bnf_borrow_controller import present_paris_borrow
from cifereca.models import Account, SearchResult
from tests.test_sync_state_contract import _service
from tests.test_core_p0_2055_bnf_ui_consent import DialogApi, view

ROOT = Path(__file__).resolve().parents[1]
PROVIDER = ROOT / "src/plugins/provider-paris"
if str(PROVIDER) not in sys.path:
    sys.path.insert(0, str(PROVIDER))
from cifereca_paris import ParisProviderSession


@pytest.fixture(autouse=True)
def no_ambient_proxy(monkeypatch):
    # The real client is constructed before its MockTransport is attached.
    # Host proxy settings must not add optional SOCKS dependencies or I/O.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


def setup(tmp_path, *, result="acsm", existing=False, loan_state="active"):
    service = _service(tmp_path)
    account = Account(unique_id="", identity_id="default", provider_id="paris",
                      credential_id="card-A", display_name="Synthetic Paris")
    item = SearchResult(id="paris:edition", record_id="edition", title="Synthetic",
                        docbase="DILICOM", medium="digital", scenario="EBOOKS",
                        source_provider="paris", source_credential="card-A",
                        author=None, type=None, format=None, publisher=None,
                        published_at=None, description=None, thumbnail_url=None,
                        friendly_url=None)
    session = ParisProviderSession(
        credential_id="card-A", label="Paris", credentials={}, options={},
        config={}, diagnostics=service.diagnostics,
        identity_broker=IdentityBrokerImpl(service),
    )
    session.connected = True
    session._accounts = [account]
    service.accounts = [account]
    service._sessions[service._session_key("paris", "card-A")] = session
    service.storage.provider_connection_put(dict(
        identity_id="default", provider_id="paris", credential_id="card-A",
        label="Synthetic", enabled=True, metadata={},
    ))
    service._provider_plugin_ids["paris"] = "provider.paris"
    manifest = json.loads((PROVIDER / "cifereca-plugin.json").read_text())
    plugin = manifest["plugins"][0]
    service._plugin_manager = NS(status=lambda _id: NS(
        state="ready", enabled=True, descriptor=NS(
            id=plugin["id"], type=plugin["type"], capabilities=plugin["capabilities"],
        ),
    ))
    state = {"active": existing, "calls": [], "mutations": 0, "before_holdings": lambda: None}
    href = "/numerique/Portal/ASSARedirect.ashx?url=" + quote("/fake/loan-book.ashx", safe="")
    def http(request):
        assert request.url.host == "bibliotheques.paris.fr"
        # A separately opened SQLite reader must see the durable reservation
        # before the real provider performs even its first descriptive read.
        with sqlite3.connect(service.storage.path) as db:
            raw = db.execute("SELECT value FROM metadata WHERE key=?",
                             (borrow_claim_key(account, item),)).fetchone()
        # The initial token GET belongs to the *same* borrowing action but
        # occurs only AFTER the exact loan was confirmed in the Core claim.
        # All preflight and ASSARedirect requests still require request_sent.
        expected_checkpoint = (
            "confirmed" if request.url.path.endswith("/synthetic.acsm")
            else "request_sent"
        )
        assert raw is not None and json.loads(raw[0])["status"] == expected_checkpoint
        state["calls"].append(request.url.path)
        if request.url.path.endswith("ListLoans"):
            assert request.method == "GET"
            assert request.url.params["userUniqueIdentifier"] == ""
            rows = [{"Id": "loan-exact", "RecordId": "edition", "State": loan_state,
                     "WhenBack": "/Date(1794000000000)/"}] if state["active"] else []
            if result == "duplicate" and state["active"]:
                rows *= 2
            return httpx.Response(200, json={"success": True, "d": {"Loans": rows}})
        if request.url.path.endswith("/synthetic.acsm"):
            assert request.method == "GET"
            if result in {"replay-redirect", "encoded-replay-redirect", "benign-redirect"}:
                target = (href if result == "replay-redirect" else
                          href.replace("ASSARedirect", "ASSA%52edirect")
                          if result == "encoded-replay-redirect" else
                          "/numerique/another-token.acsm")
                return httpx.Response(302, headers={"Location": target})
            return httpx.Response(200, content=(
                b'<fulfillmentToken xmlns="http://ns.adobe.com/adept">'
                b'<format>application/epub+zip</format></fulfillmentToken>'
            ), headers={"content-type": "application/vnd.adobe.adept+xml"})
        if "GetListHoldings" in request.url.path:
            assert request.method == "POST"
            state["before_holdings"]()
            if result == "preflight-failure":
                return httpx.Response(503)
            return httpx.Response(200, json={"success": True, "d": {
                "ItemHoldingsData": {"CanLoan": True},
                "HtmlView": f'<a href="{href}">Borrow</a>',
            }})
        assert request.url.path.endswith("ASSARedirect.ashx")
        assert request.method == "GET"
        state["mutations"] += 1
        state["active"] = result not in {"unconfirmed", "timeout-unconfirmed"}
        if result in {"timeout", "timeout-unconfirmed"}:
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if result == "rebind":
            service._account_context_epoch += 1
        if result == "no-acsm":
            return httpx.Response(200, text="loan created, delivery unavailable")
        return httpx.Response(302, headers={
            "Location": "https://bibliotheques.paris.fr/numerique/synthetic.acsm",
        })
    session.client.client.close()
    session.client.client = httpx.Client(transport=httpx.MockTransport(http),
                                       follow_redirects=False, trust_env=False)
    return service, session, account, item, state


def attempt(service, account, item, **kwargs):
    return service.borrow_paris_manual(
        item, expected_identity_id=account.identity_id, confirmed=True,
        expected_account=account, expected_context_epoch=service._account_context_epoch,
        **kwargs,
    )


@pytest.mark.parametrize("result", ["acsm", "no-acsm", "timeout"])
def test_single_manual_action_confirms_current_loan_independently_of_acsm(tmp_path, result):
    service, session, account, item, state = setup(tmp_path, result=result)
    first_get_attempts = []
    first_get_errors = []
    original_download = session.download_digital

    def observed_download(*args, **kwargs):
        first_get_attempts.append(kwargs.get("initial_offer_only") is True)
        try:
            return original_download(*args, **kwargs)
        except Exception as exc:
            # Only closed exception *types* are safe for CI assertions.
            first_get_errors.append(type(exc).__name__)
            raise

    session.download_digital = observed_download
    try:
        assert service.can_borrow_paris(item)
        assert state["calls"] == []  # availability is inert
        outcome = attempt(service, account, item)
        assert outcome["status"] == "confirmed" and outcome["provider_verified"] is True
        assert state["mutations"] == 1
        if result == "acsm":
            assert first_get_attempts == [True]
            assert first_get_errors == []
        assert len(state["calls"]) == (5 if result == "acsm" else 4)
        row = service.storage.loan_borrow_state(borrow_claim_key(account, item))
        assert row["verified_loan_id"] == "loan-exact" and row["status"] == "confirmed"
        assert service.storage.entitlements(provider_id="paris") == []
        assert service.storage.acquisitions() == []
        assert service.can_borrow_paris(item) is False
        assert attempt(service, account, replace(item, id="fresh-ui-row"))["reason_code"] == "borrow_previous_attempt"
        assert state["mutations"] == 1
    finally:
        service.close()


def test_existing_current_loan_confirms_without_mutating_request(tmp_path):
    service, _, account, item, state = setup(tmp_path, existing=True)
    try:
        assert attempt(service, account, item)["status"] == "confirmed"
        assert state["mutations"] == 0 and len(state["calls"]) == 1
    finally:
        service.close()


@pytest.mark.parametrize("result", ["acsm", "timeout-unconfirmed", "duplicate", "rebind"])
def test_restart_never_replays_confirmed_or_uncertain_mutation(tmp_path, result):
    service, _, account, item, state = setup(tmp_path, result=result)
    try:
        outcome = attempt(service, account, item)
        assert outcome["status"] == ("confirmed" if result == "acsm" else "verification_unavailable")
        assert state["mutations"] == 1
    finally:
        service.close()
    restarted, _, account, item, state = setup(tmp_path)
    try:
        assert not restarted.can_borrow_paris(replace(item, id="new-result-row"))
        assert attempt(restarted, account, item)["reason_code"] == "borrow_previous_attempt"
        assert state["calls"] == []
    finally:
        restarted.close()


def test_proven_unsent_failure_releases_only_its_claim_for_new_manual_click(tmp_path):
    service, _, account, item, state = setup(tmp_path, result="preflight-failure")
    try:
        assert attempt(service, account, item)["reason_code"] == "borrow_not_submitted"
        assert state["mutations"] == 0
        assert service.storage.metadata_get(borrow_claim_key(account, item)) is None
        assert service.can_borrow_paris(item)
    finally:
        service.close()


@pytest.mark.parametrize("field,value", [
    ("source_provider", "bnf-pret"), ("source_credential", "other"),
    ("docbase", "SYRACUSE"), ("medium", "physical"), ("record_id", ""),
])
def test_foreign_source_or_branch_is_rejected_before_read_or_claim(tmp_path, field, value):
    service, _, account, item, state = setup(tmp_path)
    try:
        candidate = replace(item, **{field: value})
        assert not service.can_borrow_paris(candidate)
        assert attempt(service, account, candidate)["status"] == "blocked"
        assert state["calls"] == []
        assert service.storage.metadata_get(borrow_claim_key(account, item)) is None
    finally:
        service.close()


@pytest.mark.parametrize("confirmation", [False, None, 1, "yes"])
def test_confirmation_must_be_exact_true_before_any_provider_io(tmp_path, confirmation):
    service, _, account, item, state = setup(tmp_path)
    try:
        assert service.borrow_paris_manual(item, expected_identity_id="default",
            confirmed=confirmation)["reason_code"] == "borrow_confirmation_required"
        assert state["calls"] == []
    finally:
        service.close()


@pytest.mark.parametrize("change", ["identity", "credential", "borrower", "connection", "epoch"])
def test_scope_revoked_during_read_never_reaches_mutating_hop(tmp_path, change):
    service, _, account, item, state = setup(tmp_path)
    def revoke():
        if change == "identity":
            account.identity_id = "alex"
        elif change == "credential":
            account.credential_id = "other"
        elif change == "borrower":
            account.unique_id = "linked-reader"
        elif change == "connection":
            service.storage.provider_connection_put(dict(
                identity_id="alex", provider_id="paris", credential_id="card-A",
                label="Synthetic", enabled=True, metadata={},
            ))
        else:
            service._account_context_epoch += 2
    state["before_holdings"] = revoke
    try:
        assert attempt(service, account, item)["status"] != "confirmed"
        assert state["mutations"] == 0
    finally:
        service.close()


def test_ui_click_queues_once_and_confirms_through_real_core_and_adapter(tmp_path):
    service, session, account, item, state = setup(tmp_path)
    try:
        window, errors, messages, refreshes = view(service, session, account, item)
        for _ in range(2):
            present_paris_borrow(window, item, None, Adw=DialogApi,
                                 TaskPriority=NS(USER_ACTION=object()))
        assert len(window.runner.tasks) == 1 and state["calls"] == []
        assert window.runner.complete_one()["status"] == "confirmed"
        assert state["mutations"] == 1 and errors == []
        assert messages == ["Prêt Paris actif vérifié dans Mes emprunts"]
        assert refreshes == [True]
    finally:
        service.close()


@pytest.mark.parametrize("change", ["selection", "identity", "notice", "epoch"])
def test_ui_change_before_queued_work_creates_no_claim(tmp_path, change):
    service, session, account, item, state = setup(tmp_path)
    try:
        window, errors, messages, refreshes = view(service, session, account, item)
        present_paris_borrow(window, item, None, Adw=DialogApi,
                             TaskPriority=NS(USER_ACTION=object()))
        assert len(window.runner.tasks) == 1
        if change == "selection":
            window._selected_search_id = "other"
        elif change == "identity":
            window._identity_context_generation += 2
        elif change == "notice":
            item.title = "Other title"
        else:
            service._account_context_epoch += 2
        assert window.runner.complete_one()["status"] == "blocked"
        assert state["calls"] == []
        assert service.storage.metadata_get(borrow_claim_key(account, item)) is None
        assert messages == refreshes == []
    finally:
        service.close()


def test_mainwindow_routes_paris_through_claim_controller_never_legacy_borrow(tmp_path):
    source = ROOT / "src/cifereca/gui/main_window.py"
    tree = ast.parse(source.read_text())
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "MainWindow")
    methods = [x for x in cls.body if isinstance(x, ast.FunctionDef)
               and x.name in {"can_borrow_item", "borrow_search_item"}]
    namespace = {"SearchResult": SearchResult, "Gtk": NS(Widget=object),
                 "Adw": DialogApi, "TaskPriority": NS(USER_ACTION=object()),
                 "__name__": "cifereca.gui.main_window"}
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
                 str(source), "exec"), namespace)
    service, session, account, item, state = setup(tmp_path)
    try:
        window, errors, messages, refreshes = view(service, session, account, item)
        assert namespace["can_borrow_item"](window, item)
        namespace["borrow_search_item"](window, item)
        assert len(window.runner.tasks) == 1
        assert window.runner.complete_one()["status"] == "confirmed"
        assert state["mutations"] == 1 and errors == []
    finally:
        service.close()


@pytest.mark.parametrize("loan_state", [None, 2])
def test_current_list_state_is_kept_verbatim_without_inventing_rights(tmp_path, loan_state):
    service, _, account, item, state = setup(tmp_path, loan_state=loan_state)
    try:
        assert attempt(service, account, item)["status"] == "confirmed"
        assert state["mutations"] == 1
        assert service.storage.entitlements(provider_id="paris") == []
    finally:
        service.close()


def test_ui_never_remaps_explicit_foreign_account_to_primary(tmp_path):
    service, session, account, item, state = setup(tmp_path)
    try:
        window, errors, messages, refreshes = view(service, session, account, item)
        present_paris_borrow(window, item, None, Adw=DialogApi,
                             TaskPriority=NS(USER_ACTION=object()),
                             account=replace(account, unique_id="linked-reader"))
        assert not window.runner.tasks and state["calls"] == []
    finally:
        service.close()


def test_first_offer_staged_and_reused_without_reborrowing_or_reusing_url(tmp_path, monkeypatch):
    """First signed URL -> private ACSM -> stable receipt -> later acquisition.

    In particular, an inventory loan lacking that one-shot URL must still
    reuse the staged bytes and cannot trigger another ASSARedirect.
    """
    from unittest.mock import Mock
    from cifereca import acsm_cache

    service, session, account, item, state = setup(tmp_path, result="acsm")
    token = (b'<fulfillmentToken xmlns="http://ns.adobe.com/adept">'
             b'<format>application/epub+zip</format></fulfillmentToken>')
    stage_calls = []
    staged_loans = []

    def first_token(loan, owner, directory, *, admission_current=None,
                    initial_offer_only=False):
        assert initial_offer_only is True
        assert admission_current is not None
        assert admission_current()
        assert owner is account
        assert loan.record_id == item.record_id
        assert loan.ebook_url.endswith("synthetic.acsm")
        stage_calls.append("GET-first-offer")
        staged_loans.append(loan)
        path = directory / "first-offer.acsm"
        path.write_bytes(token)
        return path, ""

    monkeypatch.setattr(session, "download_digital", first_token)
    try:
        outcome = attempt(service, account, item)
        assert outcome["status"] == "confirmed"
        assert outcome["provider_verified"] is True
        assert stage_calls == ["GET-first-offer"]
        assert state["mutations"] == 1
        assert len(state["calls"]) == 4

        later_loan = replace(staged_loans[0], ebook_url=None)
        directory = service._acquisition_work_dir("default", "paris", "loan-exact")
        staged = acsm_cache.reuse_initial(
            directory, later_loan, "default", "primary",
            claim_state=service.storage.loan_borrow_state)
        assert staged is not None and staged.read_bytes() == token
        # The receipt is not authority by itself: a missing durable claim
        # cannot be promoted to an acquisition, even with matching bytes.
        assert acsm_cache.reuse_initial(
            directory, later_loan, "default", "primary",
            claim_state=lambda _key: None) is None
        service.acquisition.run_post_download = Mock()
        outcome2 = service.acquire_digital_loan(later_loan)
        assert outcome2.kind == "acsm"
        assert outcome2.path == staged
        assert state["mutations"] == 1 and stage_calls == ["GET-first-offer"]
        service.acquisition.run_post_download.assert_called_once()
        # A subsequent read uses standard post-hook cache; no second hook.
        service.acquire_digital_loan(later_loan)
        service.acquisition.run_post_download.assert_called_once()
    finally:
        service.close()


def test_first_offer_get_failure_does_not_repeat_or_unconfirm_borrow(tmp_path, monkeypatch):
    service, session, account, item, state = setup(tmp_path, result="acsm")
    attempts = []

    def unavailable(*_args, **_kwargs):
        attempts.append("GET")
        raise RuntimeError("supplier first token unavailable")

    monkeypatch.setattr(session, "download_digital", unavailable)
    try:
        outcome = attempt(service, account, item)
        assert outcome["status"] == "confirmed"
        assert outcome["provider_verified"] is True
        assert attempts == ["GET"]
        assert state["mutations"] == 1
        assert attempt(service, account, item)["reason_code"] == "borrow_previous_attempt"
        assert attempts == ["GET"]
        assert state["mutations"] == 1
    finally:
        service.close()


@pytest.mark.parametrize("result", ["replay-redirect", "encoded-replay-redirect"])
def test_first_token_redirect_cannot_reenter_mutating_assaredirect(tmp_path, result):
    """A signed token GET returning a loan endpoint is rejected without replay."""
    from cifereca import acsm_cache

    service, _session, account, item, state = setup(
        tmp_path, result=result)
    try:
        outcome = attempt(service, account, item)
        assert outcome["status"] == "confirmed"
        assert outcome["provider_verified"] is True
        # The first request creates the loan. The redirected second one is
        # refused *before* any HTTP request to the mutating endpoint.
        assert state["mutations"] == 1
        assert len(state["calls"]) == 5
        stage = service._acquisition_work_dir("default", "paris", "loan-exact")
        assert not (stage / acsm_cache.INITIAL_MANIFEST).exists()
        assert attempt(service, account, item)["reason_code"] == "borrow_previous_attempt"
        assert state["mutations"] == 1
    finally:
        service.close()


def test_initial_offer_never_follows_even_a_benign_redirect(tmp_path):
    """Only the supplied first-offer URL may be read, never redirect targets."""
    from cifereca import acsm_cache

    service, _session, account, item, state = setup(
        tmp_path, result="benign-redirect")
    try:
        outcome = attempt(service, account, item)
        assert outcome["status"] == "confirmed"
        assert outcome["provider_verified"] is True
        assert state["mutations"] == 1
        assert state["calls"].count("/numerique/synthetic.acsm") == 1
        assert "/numerique/another-token.acsm" not in state["calls"]
        stage = service._acquisition_work_dir("default", "paris", "loan-exact")
        assert not (stage / acsm_cache.INITIAL_MANIFEST).exists()
        assert attempt(service, account, item)["reason_code"] == "borrow_previous_attempt"
        assert state["mutations"] == 1
    finally:
        service.close()


def test_first_acsm_epoch_rebind_after_get_does_not_publish_claimed_receipt(
        tmp_path, monkeypatch):
    """A verified borrow stays confirmed; revoked context cannot stage bytes."""
    from cifereca import acsm_cache

    service, session, account, item, state = setup(tmp_path, result="acsm")
    attempts = []

    def rebind_after_get(*args, **kwargs):
        assert kwargs["initial_offer_only"] is True
        assert kwargs["admission_current"]() is True
        attempts.append("token-get")
        service._account_context_epoch += 1
        directory = args[2]
        candidate = directory / "first.acsm"
        candidate.write_bytes(
            b'<fulfillmentToken xmlns="http://ns.adobe.com/adept">'
            b'<format>application/epub+zip</format></fulfillmentToken>'
        )
        return candidate, ""

    monkeypatch.setattr(session, "download_digital", rebind_after_get)
    try:
        outcome = attempt(service, account, item)
        assert outcome["status"] == "confirmed"
        assert outcome["provider_verified"] is True
        assert state["mutations"] == 1
        assert attempts == ["token-get"]
        directory = service._acquisition_work_dir(
            "default", "paris", "loan-exact")
        assert not (directory / acsm_cache.INITIAL_MANIFEST).exists()
    finally:
        service.close()
