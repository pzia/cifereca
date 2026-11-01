"""Fail-closed, read-only inspection of the BnF Web borrow flow (#2055).

This module never sends HTTP requests, grants a loan, or exposes a CSRF token
as catalogue metadata or diagnostics. Inputs are *fresh* portal HTML, not
saved Browser Recorder fixtures. A form's presence is not permission to POST.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from urllib.parse import urljoin, urlsplit

from cifereca_bnf_pret_web import ORIGIN, WebParseError, _dom, _id

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_\[\]-]{0,127}\Z")
_BORROW_SEGMENT = re.compile(
    r"(?:^|/)(?:borrow|borrows|loan|loans|checkout|checkouts|activation|activate|confirm|confirmation)(?:/|$)",
    re.IGNORECASE,
)
_FORBIDDEN_FIELD = frozenset({
    "password", "samlresponse", "_method", "method", "redirect_to", "return_url",
})
_MAX_QUERY = 4096


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
                or not _BORROW_SEGMENT.search(target.path)
                or any(target.path.startswith(p) for p in (
                    "/v1/", "/sso/", "/my_profile/", "/logout", "/admin/"
                ))):
            raise ValueError
        # A path naming a *different* resource must never authorize a loan.
        resource_path = re.search(r"(?:^|/)resources/([^/]+)", target.path)
        if resource_path and resource_path.group(1) != resource_id:
            raise ValueError
    except (ValueError, TypeError):
        raise WebParseError("borrow_url_refused") from None
    return url


def confirmation_link(html: str, *, page_url: str, resource_id: str) -> str:
    """Get the unique enabled borrow link inside the exact Book microdata.

    Button counts, JavaScript handlers and catalogue availability are never
    promoted to a Web action.
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
    if (button.tag != "a" or not button.closed
            or not button.attrs.get("href") or "disabled" in button.attrs
            or button.attrs.get("aria-disabled", "").lower() == "true"
            or any(n.has_class("lb_unavailable") for n in books[0].walk())):
        raise WebParseError("borrow_action_unverified")
    return _transition_url(page_url, button.attrs["href"], rid)


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
