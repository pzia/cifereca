"""Fail-closed, read-only inspection of the BnF Web borrow flow (#2055).

This module never sends HTTP requests, grants a loan, or exposes a CSRF token
as catalogue metadata or diagnostics. Inputs are *fresh* portal HTML, not
saved Browser Recorder fixtures. A form's presence is not permission to POST.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

from cifereca_bnf_pret_web import ORIGIN, WebParseError, _dom, _id

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_\[\]-]{0,127}\Z")
_BORROW_INTENT = frozenset({
    "borrow", "borrows", "checkout", "checkouts",
    "activation", "activate", "confirm", "confirmation", "new", "create",
})
_FORBIDDEN_ROUTE = frozenset({
    "return", "returns", "renew", "renewal", "cancel", "delete",
    "download", "token", "acsm", "acs4", "reader",
    "logout", "sso", "admin", "v1", "v2",
})
_FORBIDDEN_FIELD = frozenset({
    "password", "samlresponse", "_method", "method", "redirect_to", "return_url",
})
_MAX_QUERY = 4096
_MEDIA_CONFIRMATION = re.compile(r"/media/([A-Za-z0-9_-]{1,512})/loans_confirmation\Z")
_MEDIA_ACTIVATION = re.compile(r"/media/([A-Za-z0-9_-]{1,512})/loans_activation\Z")


def _is_media_confirmation(url: str) -> bool:
    try:
        return _MEDIA_CONFIRMATION.fullmatch(urlsplit(url).path) is not None
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True, repr=False)
class BorrowForm:
    """Ephemeral POST candidate; NEVER an authorized action or public offer."""
    action_url: str = field(repr=False)
    fields: tuple[tuple[str, str], ...] = field(repr=False)

    def __repr__(self) -> str:
        return "BorrowForm(authorization=unverified)"


def _resource_id(value: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise WebParseError("borrow_resource_invalid")
    return value


def _transition_url(current_url: str, href: str, resource_id: str) -> str:
    """Validate an observed portal-only URL without fabricating an endpoint.

    The path must denote an actual loan/borrow action; ordinary resource, API,
    SSO, profile, logout and downloads never qualify as borrow transitions.
    """
    _resource_id(resource_id)
    if (type(current_url) is not str or type(href) is not str
            or not href or len(href) > 4096 or href.startswith("//")
            or any(ord(c) < 33 or ord(c) > 126 or c == "\\" for c in href)):
        raise WebParseError("borrow_url_refused")
    try:
        base = urlsplit(current_url)
        url = urljoin(current_url, href)
        target = urlsplit(url)
        if (base.scheme != "https" or base.netloc != "pret.bnf.fr"
                or target.scheme != "https" or target.netloc != "pret.bnf.fr"
                or target.username or target.password or target.fragment
                or not target.path.startswith("/") or "//" in target.path
                or "%" in target.path or "/./" in target.path or "/../" in target.path
                or len(target.query) > _MAX_QUERY
                or any(target.path.startswith(p) for p in (
                    "/v1/", "/v2/", "/sso/", "/my_profile/", "/logout", "/admin/"
                ))):
            raise ValueError
        segments = [segment.casefold()
                    for segment in target.path.split("/") if segment]
        # /loans/<opaque-id> is a *token retrieval*, not a confirmation GET.
        # A bare "loans" segment must never be enough to authorize a visit.
        media_confirmation = _MEDIA_CONFIRMATION.fullmatch(target.path)
        media_activation = _MEDIA_ACTIVATION.fullmatch(target.path)
        if media_activation:
            prior = _MEDIA_CONFIRMATION.fullmatch(base.path)
            if (prior is None or media_activation.group(1) != prior.group(1)
                    or target.query):
                raise ValueError
        if not (media_confirmation or media_activation):
            if (not segments or not any(s in _BORROW_INTENT for s in segments)
                    or any(s in _FORBIDDEN_ROUTE for s in segments)):
                raise ValueError
            if (segments[0] not in {"resources", "loans", "borrow", "borrows",
                                   "checkout", "checkouts", "activation",
                                   "confirm", "confirmation"}):
                raise ValueError
            if (segments[0] == "resources" and
                    (len(segments) < 3 or target.path.split('/')[2] != resource_id)):
                raise ValueError
        # A path naming a *different* resource must never authorize a loan.
        resource_path = re.search(r"(?:^|/)resources/([^/]+)", target.path)
        if resource_path and resource_path.group(1) != resource_id:
            raise ValueError
        # Query coordinates can carry the selected resource. An action
        # pointing at a different book must never be admitted just because
        # the visible button lives inside the selected Book card.
        pairs = parse_qsl(
            target.query, keep_blank_values=True, strict_parsing=True,
            encoding="utf-8", errors="strict", max_num_fields=32,
        ) if target.query else []
        selected = [v for k, v in pairs if k in {
            "resource_id", "resource", "book_id", "_resource_id",
        }]
        if (len(selected) > 1 or any(v != resource_id for v in selected)):
            raise ValueError
        if media_confirmation:
            params = dict(pairs)
            if (len(pairs) != 3 or len(params) != 3
                    or set(params) != {"_resource_id", "notify", "email"}
                    or params["_resource_id"] != resource_id
                    or params["notify"] != "1"
                    or not 3 <= len(params["email"]) <= 254
                    or params["email"].count("@") != 1
                    or any(ord(c) <= 32 or ord(c) >= 127
                           for c in params["email"])):
                raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise WebParseError("borrow_url_refused") from None
    return url


def confirmation_link(html: str, *, page_url: str, resource_id: str) -> str:
    """Get the unique enabled borrow link inside the exact Book microdata.

    The only actionable signal is one *enabled* exact-resource borrow link
    after the reader's manual confirmation. A generic lb_unavailable badge
    can coexist with an active borrow control in captured BnF pages: treat
    it as an informational P2 marker, NOT a veto. BnF itself decides quota,
    copies and rights when the confirmed transaction reaches its server.
    Button counts and catalogue availability alone are never a Web action.
    """
    rid = _resource_id(resource_id)
    if page_url != ORIGIN + "/resources/" + rid:
        raise WebParseError("borrow_scope_mismatch")
    root = _dom(html)
    books = [node for node in root.walk() if "itemscope" in node.attrs
             and any(t in {"http://schema.org/Book", "https://schema.org/Book"}
                     for t in node.attrs.get("itemtype", "").split())]
    if len(books) != 1 or not books[0].closed:
        raise WebParseError("borrow_detail_unrecognized")
    controls = [node for node in books[0].walk()
                if node.has_class("button-borrow")]
    if len(controls) != 1:
        raise WebParseError("borrow_action_unverified")
    button = controls[0]
    if (not button.closed or "disabled" in button.attrs
            or button.attrs.get("aria-disabled", "").lower() == "true"):
        raise WebParseError("borrow_action_unverified")
    if button.tag == "a":
        if not button.attrs.get("href"):
            raise WebParseError("borrow_action_unverified")
        return _transition_url(page_url, button.attrs["href"], rid)
    # Observed on pret.bnf.fr: a submit button inside a GET form scoped to
    # this Book. Read the exact action/hidden fields; never synthesize a route.
    if (button.tag != "button" or button.attrs.get("type", "").lower() != "submit"
            or any(key in button.attrs for key in
                   ("form", "formaction", "formmethod", "formtarget", "formenctype"))):
        raise WebParseError("borrow_action_unverified")
    forms = [node for node in books[0].walk()
             if node.tag == "form" and node.closed
             and any(child is button for child in node.walk())]
    if len(forms) != 1:
        raise WebParseError("borrow_action_unverified")
    form = forms[0]
    if (form.attrs.get("method", "").lower() != "get"
            or any(key in form.attrs for key in ("target", "enctype"))
            or form.attrs.get("accept-charset", "UTF-8").upper() != "UTF-8"):
        raise WebParseError("borrow_action_unverified")
    fields: dict[str, str] = {}
    for node in form.walk():
        if node is form or node is button:
            continue
        if node.tag == "input":
            if (node.attrs.get("type", "").lower() != "hidden"
                    or "disabled" in node.attrs
                    or node.attrs.get("name", "") not in
                    {"_resource_id", "notify", "email"}
                    or node.attrs["name"] in fields):
                raise WebParseError("borrow_action_unverified")
            fields[node.attrs["name"]] = node.attrs.get("value", "")
        elif node.tag in {"button", "select", "textarea"}:
            raise WebParseError("borrow_action_unverified")
    if set(fields) != {"_resource_id", "notify", "email"}:
        raise WebParseError("borrow_action_unverified")
    # The route, selected resource and query fields are all checked again
    # at the transport boundary. Never log/return the raw form object.
    action = form.attrs.get("action", "")
    if not action or "?" in action or "#" in action:
        raise WebParseError("borrow_action_unverified")
    return _transition_url(page_url, action + "?" + urlencode(fields), rid)


def activation_form(html: str, *, page_url: str,
                    resource_id: str) -> BorrowForm:
    """Inspect an observed confirmation screen, without executing its POST.

    Requires a single bound resource and a single complete POST form with a
    fresh hidden CSRF field. All non-hidden, duplicate and method-override
    inputs are rejected rather than submitted with invented defaults.
    """
    rid = _resource_id(resource_id)
    _transition_url(ORIGIN + "/resources/" + rid, page_url, rid)
    root = _dom(html)
    # Do not submit a confirmation page that might refer to another resource.
    resource_links = {_id(n.attrs.get("href", ""), "resources")
                      for n in root.walk() if n.tag == "a"}
    resource_links.discard(None)
    if resource_links != {rid}:
        raise WebParseError("borrow_scope_mismatch")
    forms = [n for n in root.walk() if n.tag == "form" and n.closed
             and n.attrs.get("method", "").lower() == "post"]
    if len(forms) != 1:
        raise WebParseError("borrow_form_unverified")
    form = forms[0]
    action = _transition_url(page_url, form.attrs.get("action", ""), rid)
    fields: dict[str, str] = {}
    for node in form.walk():
        if node.tag not in {"input", "select", "textarea", "button"}:
            continue
        if node.tag != "input":
            if node.attrs.get("name"):
                raise WebParseError("borrow_form_unverified")
            continue
        if ("disabled" in node.attrs or node.attrs.get("type", "").lower() != "hidden"):
            # Submit buttons without names are harmless; no implicit selection.
            if (node.attrs.get("type", "").lower() == "submit"
                    and not node.attrs.get("name")):
                continue
            raise WebParseError("borrow_form_unverified")
        name, value = node.attrs.get("name", ""), node.attrs.get("value", "")
        if (_FIELD.fullmatch(name) is None or name.lower() in _FORBIDDEN_FIELD
                or name in fields or len(fields) >= 32
                or len(value) > 4096 or any(ord(c) < 32 for c in value)):
            raise WebParseError("borrow_form_unverified")
        fields[name] = value
    if not fields.get("authenticity_token"):
        raise WebParseError("borrow_csrf_unverified")
    for name in ("resource_id", "resource", "book_id"):
        if name in fields and fields[name] != rid:
            raise WebParseError("borrow_scope_mismatch")
    return BorrowForm(action, tuple(fields.items()))


@dataclass(frozen=True, repr=False)
class BorrowActivationProof:
    """A positive *active* upstream loan observed independently after activation.

    Not evidence that a fresh loan was created or that an ACSM was fulfilled:
    the BnF inventory is intrinsically partial. Never log the opaque IDs.
    """
    loan_id: str = field(repr=False)
    resource_id: str = field(repr=False)

    def __repr__(self) -> str:
        return "BorrowActivationProof(active_loan_observed=True)"


def verify_borrow_activation(
    activation_html: str, activity_html: str, *, resource_id: str,
    before, expected_scope,
) -> BorrowActivationProof:
    """Verify one activation receipt against the *fresh* active-loan section.

    The remote response and subsequent reader activity are independent HTML
    observations; an HTTP 200 or a successful form submission alone is never
    a confirmed result. The before snapshot can only flag a known existing
    loan, not establish absence from a partial inventory.
    """
    from cifereca_bnf_pret_web import (
        LoanSnapshot, ProfileScope, parse_loans,
    )
    rid = _resource_id(resource_id)
    if (type(expected_scope) is not ProfileScope
            or type(before) is not LoanSnapshot
            or before.scope != expected_scope):
        raise WebParseError("borrow_scope_mismatch")
    # Prior presence is a *positive* refusal, not a negative availability
    # claim when the earlier page happened to omit a loan.
    if any(row.resource_id == rid for row in before.loans):
        raise WebParseError("borrow_existing_loan")
    receipt = _dom(activation_html)
    ids = {_id(n.attrs.get("href", ""), "loans")
           for n in receipt.walk() if n.tag == "a" and n.closed}
    ids.discard(None)
    if len(ids) != 1:
        raise WebParseError("borrow_receipt_unverified")
    loan_id = next(iter(ids))
    observed = parse_loans(
        activity_html, page_url=ORIGIN + "/my_profile/activity",
        expected=expected_scope, observed=expected_scope,
    )
    # A partial parser result can contain positives, but it cannot bind an
    # ambiguous activation to an exact loan safely.
    if observed.status != "observed":
        raise WebParseError("borrow_activity_unverified")
    matches = [row for row in observed.loans
               if row.loan_id == loan_id and row.resource_id == rid]
    conflicting = [row for row in observed.loans
                   if row.loan_id == loan_id or row.resource_id == rid]
    if len(matches) != 1 or len(conflicting) != 1:
        raise WebParseError("borrow_receipt_unverified")
    # Prior page was partial, so a pre-existing loan may have been omitted;
    # do not label this record proof of *creation*, quota or eligibility.
    return BorrowActivationProof(loan_id, rid)
