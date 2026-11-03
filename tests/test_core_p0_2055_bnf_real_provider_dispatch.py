"""#2055: CoreUI → real BnF provider → HTTPX synthetic lender integration.

No live BnF requests, credentials, SAML, ACSM, or remote mutation.
The one-click CoreUI action, Core durable checkpoint, real provider parser and
real network transport boundary are integrated, not separately mocked.
"""
from pathlib import Path
import sys

import httpx
from cifereca.core.borrow_action_scope import borrow_claim_key
from tests.test_core_p0_2055_manual_dispatch import setup
from tests.test_core_p0_2055_bnf_ui_consent import (
    DialogApi, request, view,
)

REPO = Path(__file__).resolve().parents[1]
PROVIDER = REPO / "src" / "plugins" / "provider-bnf-pret"
if str(PROVIDER) not in sys.path:
    sys.path.insert(0, str(PROVIDER))

from cifereca_bnf_pret import BnfProviderSession
from cifereca_bnf_pret_http import ReaderHTTPClient
from cifereca_bnf_pret_web import CatalogRecord

RID = "synthetic-book"
BEFORE = '<section class="active-loans"></section>'
AFTER = (
    '<section class="active-loans"><div class="js-loan">'
    '<a href="/resources/synthetic-book">Synthetic title</a>'
    '<a href="/loans/loan-synthetic">Open loan</a>'
    '</div></section>'
)
DETAIL = (
    '<article itemscope itemtype="https://schema.org/Book">'
    '<a class="button-borrow" href="/resources/synthetic-book/loans/new">'
    'Emprunter</a></article>'
)
CONFIRM = (
    '<a href="/resources/synthetic-book">Book</a>'
    '<form method="post" action="/loans/activation">'
    '<input type="hidden" name="authenticity_token" value="synthetic-csrf">'
    '<input type="hidden" name="resource_id" value="synthetic-book">'
    '</form>'
)
RECEIPT = '<a href="/loans/loan-synthetic">Prêt obtenu</a>'


def real_session_service(tmp_path, *, post_status=200, after=AFTER,
                         detail=DETAIL):
    service, synthetic, account, _ = setup(tmp_path)
    # A local MockTransport validates every requested path. There is no
    # HTTP connection to the real pret.bnf.fr server.
    state = {"post": 0, "calls": []}

    def handler(req):
        state["calls"].append((req.method, req.url.path))
        assert req.url.host == "pret.bnf.fr"
        if req.method == "GET" and req.url.path == "/my_profile/activity":
            return httpx.Response(
                200, text=after if state["post"] else BEFORE,
                headers={"content-type": "text/html"},
            )
        if req.method == "GET" and req.url.path == "/resources/" + RID:
            return httpx.Response(
                200, text=detail, headers={"content-type": "text/html"},
            )
        if req.method == "GET" and req.url.path == (
                "/resources/" + RID + "/loans/new"):
            return httpx.Response(
                200, text=CONFIRM, headers={"content-type": "text/html"},
            )
        if req.method == "POST" and req.url.path == "/loans/activation":
            state["post"] += 1
            assert b"authenticity_token=synthetic-csrf" in req.content
            assert b"resource_id=synthetic-book" in req.content
            return httpx.Response(
                post_status, text=RECEIPT,
                headers={"content-type": "text/html"},
            )
        raise AssertionError("unexpected or unsafe BnF request")

    client = ReaderHTTPClient(transport=httpx.MockTransport(handler))
    session = BnfProviderSession(
        client=client, credential_id=account.credential_id,
        label="Synthetic BnF", credentials={}, options={},
    )
    session._accounts = [account]
    session.connected = True
    item = session._search_result(CatalogRecord(RID, "Synthetic book"))
    service._sessions[
        service._session_key("bnf-pret", account.credential_id)
    ] = session
    return service, session, account, item, state


def test_full_coreui_core_real_provider_one_confirmed_post(tmp_path):
    service, session, account, item, state = real_session_service(tmp_path)
    try:
        assert service.can_borrow_bnf(item) is True
        window, errors, messages, refreshes = view(
            service, session, account, item,
        )
        DialogApi.instance = None
        request(window, item)
        assert DialogApi.instance is None  # no website-check popup
        assert len(window.runner.tasks) == 1
        assert state["calls"] == []  # no network until worker admission
        request(window, item)  # rapid second click cannot queue a second POST
        assert len(window.runner.tasks) == 1

        outcome = window.runner.complete_one()
        assert outcome["status"] == "confirmed"
        assert outcome["provider_verified"] is True
        assert state["post"] == 1
        assert state["calls"] == [
            ("GET", "/my_profile/activity"),
            ("GET", "/resources/" + RID),
            ("GET", "/resources/" + RID + "/loans/new"),
            ("POST", "/loans/activation"),
            ("GET", "/my_profile/activity"),
        ]
        assert service.can_borrow_bnf(item) is False
        row = service.storage.loan_borrow_state(
            borrow_claim_key(account, item))
        assert row["status"] == "confirmed"
        assert row["verified_loan_id"] == "loan-synthetic"
        assert messages == ["Prêt BnF actif vérifié dans Mes emprunts"]
        assert refreshes == [True]
        assert errors == []
        # The generic entrypoint cannot bypass this dedicated consent path.
        from cifereca.plugin_api import ProviderCapabilityError
        try:
            session.borrow(item, account)
        except ProviderCapabilityError as exc:
            assert str(exc) == "bnf_borrow_requires_core_dispatch"
        else:
            raise AssertionError("legacy BnF borrow executed")
        assert state["post"] == 1
    finally:
        service.close()


def test_real_provider_refusal_is_not_retried_or_claimed_success(tmp_path):
    service, session, account, item, state = real_session_service(
        tmp_path, post_status=403,
    )
    try:
        outcome = service.borrow_bnf_manual(
            item, expected_identity_id="default",
            confirmed=True, site_checked=True,
            expected_context_epoch=service._account_context_epoch,
            expected_account=account,
        )
        assert outcome["status"] == "verification_unavailable"
        assert outcome["provider_verified"] is False
        assert state["post"] == 1
        assert service.can_borrow_bnf(item) is False
        row = service.storage.loan_borrow_state(
            borrow_claim_key(account, item))
        assert row["status"] == "verification_unavailable"
        retry = service.borrow_bnf_manual(
            item, expected_identity_id="default",
            confirmed=True, site_checked=True,
        )
        assert retry["reason_code"] == "borrow_previous_attempt"
        assert state["post"] == 1
        assert len(state["calls"]) == 4  # activity/detail/form/POST only
    finally:
        service.close()


def test_real_provider_without_explicit_click_never_reads_or_claims(tmp_path):
    service, session, account, item, state = real_session_service(tmp_path)
    try:
        for confirmed in (False, None, "true", 1):
            outcome = service.borrow_bnf_manual(
                item, expected_identity_id="default", confirmed=confirmed,
            )
            assert outcome["reason_code"] == "borrow_confirmation_required"
        assert state["calls"] == []
        assert state["post"] == 0
        assert service.can_borrow_bnf(item)
        assert service.storage.loan_borrow_state(
            borrow_claim_key(account, item)) is None
    finally:
        service.close()


def test_disabled_book_control_before_post_frees_claim_for_later_human_action(tmp_path):
    """A disabled action cannot permanently lock the edition without POST."""
    disabled = DETAIL.replace(
        '<a class="button-borrow"', '<a disabled class="button-borrow"',
    )
    service, session, account, item, state = real_session_service(
        tmp_path, detail=disabled,
    )
    try:
        outcome = service.borrow_bnf_manual(
            item, expected_identity_id="default",
            confirmed=True, site_checked=True,
            expected_context_epoch=service._account_context_epoch,
            expected_account=account,
        )
        assert outcome["status"] == "blocked"
        assert outcome["reason_code"] == "borrow_not_submitted"
        assert state["post"] == 0
        assert state["calls"] == [
            ("GET", "/my_profile/activity"),
            ("GET", "/resources/" + RID),
        ]
        key = borrow_claim_key(account, item)
        assert service.storage.metadata_get(key) is None
        assert service.can_borrow_bnf(item) is True
        assert not service.storage.acquisitions()
        assert not service.storage.entitlements(provider_id="bnf-pret")
    finally:
        service.close()
