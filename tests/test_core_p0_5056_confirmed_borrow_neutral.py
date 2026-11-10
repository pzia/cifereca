"""P0 #5056: verified loan detail refresh is not selected by provider name."""
import ast
from copy import copy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest


CONTROLLER = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/bnf_borrow_controller.py")


class Account:
    def __init__(self, provider_id):
        self.identity_id = "alice"
        self.provider_id = provider_id
        self.credential_id = "credential-a"
        self.unique_id = "borrower-a"
        self.is_primary = True


class SearchResult:
    def __init__(self, provider_id):
        self.id = "notice"
        self.record_id = "edition-a"
        self.docbase = "synthetic"
        self.medium = "digital"
        self.source_provider = provider_id
        self.source_credential = "credential-a"
        self.source_collection = "search"
        self.title = "A book"


def _controller():
    tree = ast.parse(CONTROLLER.read_text(encoding="utf-8"))
    method = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_present_manual_borrow")
    namespace = {"Account": Account, "SearchResult": SearchResult, "copy": copy}
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[method], type_ignores=[])), str(CONTROLLER), "exec"), namespace)
    return namespace["_present_manual_borrow"]


@pytest.mark.parametrize("provider_id", ["synthetic-alpha", "synthetic-beta", "unknown"])
@pytest.mark.parametrize("outcome", ["confirmed", "unverified", "blocked", "stale"])
def test_only_current_verified_borrow_reprojects_exact_detail(provider_id, outcome):
    account = Account(provider_id)
    item = SearchResult(provider_id)
    session = NS(connected=True, _epoch=12)
    identity = NS(identity_id="alice")
    refreshed, loans, requests, messages = [], [], [], []
    detail = NS(refresh_confirmed_borrow_detail=lambda value: refreshed.append(value))
    service = NS(
        current_identity=identity,
        _account_context_epoch=9,
        accounts=[account],
        _session_for_source=lambda provider, credential: session,
    )
    window = NS(
        _closed=False,
        service=service,
        _identity_context_generation=4,
        _account_context_generation=5,
        _selected_search_id="selection-a",
        _search_detail_request_generation=6,
        _manual_borrow_pending=None,
        search_page=NS(detail=detail),
        _eligible_borrow_accounts=lambda _: (session, [account]),
        _activity_result=lambda text: messages.append(text),
        refresh_loans=lambda force: loans.append(force),
        show_error=lambda *args: messages.append(args),
    )

    def dispatch(selected, **kwargs):
        requests.append((selected, kwargs))
        if outcome == "stale":
            window._selected_search_id = "different-selection"
        if outcome == "blocked":
            return {"status": "blocked", "reason_code": "borrow_not_submitted"}
        return {"status": "confirmed", "provider_verified": outcome != "unverified"}

    def submit(_title, work, done, _failed, **_kwargs):
        done(work())

    window.runner = NS(submit=submit)
    priority = NS(USER_ACTION="user")
    _controller()(
        window, item, object(), TaskPriority=priority,
        provider_id=provider_id, label="Fournisseur",
        available=lambda _: True, dispatch=dispatch,
    )
    assert len(requests) == 1  # Never a second borrow POST.
    assert window._manual_borrow_pending is None
    if outcome == "confirmed":
        assert refreshed == [item]
        assert loans == [True]
    else:
        assert refreshed == []
        assert loans == ([] if outcome == "stale" else
                         [True] if outcome == "unverified" else [])
