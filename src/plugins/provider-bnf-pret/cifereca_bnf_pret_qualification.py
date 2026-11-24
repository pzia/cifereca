"""Private BnF DOM qualification probe (#2055): structure only, NO permission.

Accepts already-acquired HTML from a user-consented local capture. It does
not make HTTP requests, persist documents, inspect cookies, parse credentials,
follow action links, or return any text, URLs, attributes or personal IDs.

Signals are mere candidate DOM positions. They must NEVER be fed directly to
BorrowerOperationEvidence as positive permission. An identical public page
could display the same controls for an ineligible account.
"""
from __future__ import annotations

import re
from cifereca_bnf_pret_web import (
    MAX_HTML_BYTES, WebParseError, _dom, _Node,
    ORIGIN, ProfileScope, parse_loans,
)

SCHEMA = "bnf.borrow.dom-qualification/1"
PAGES = frozenset(("profile", "detail", "confirmation", "activity"))
_SAFE_TAGS = frozenset((
    "html", "body", "main", "header", "footer", "nav", "section",
    "article", "div", "span", "p", "strong", "small", "b", "em",
    "ul", "ol", "li", "a", "form", "button", "label", "table",
    "thead", "tbody", "tr", "td", "th", "h1", "h2", "h3",
))
_HIDDEN = frozenset(("script", "style", "noscript", "template"))
_QUOTA = re.compile(
    r"\bpr[eê]ts?\s+(?:num[eé]riques?\s+)?restants?\b|"
    r"\b(?:il\s+vous\s+reste|vous\s+reste)\s+.{0,35}\bpr[eê]ts?\b|"
    # Actual BnF activity wording reported in #4480: a *label* only, not
    # proof of quota, completeness, or authorization for another borrower.
    r"\bvous\s+avez\s+encore\s+droit\s+[àa]\s+[0-9]{1,3}\s+pr[eê]ts?\b",
    re.IGNORECASE,
)
_PASS = re.compile(
    r"\bpass\s+(?:bnf\s+)?(?:lecture\s*/\s*culture|recherche)\b"
    r".{0,45}\bvalid[eé]\b",
    re.IGNORECASE,
)
_COPIES = re.compile(
    r"\b(?:plusieurs|[0-9]{1,3})\s+exemplaires?\s+disponibles?\b",
    re.IGNORECASE,
)
_CONFIRM = re.compile(r"\bconfirmer\s+l[’']emprunt\b", re.IGNORECASE)

# Inline formatting may split the actually observed BnF wording without
# changing the meaning of a label. Never concatenate different block rows,
# login forms, script/style content, or unrelated edition cards.
_INLINE = frozenset(("span", "strong", "small", "b", "em", "a", "label", "i"))


def _hidden(node: _Node) -> bool:
    """Match the production parser's conservative inline visibility rules."""
    style = "".join(node.attrs.get("style", "").lower().split())
    return (node.tag in _HIDDEN or "hidden" in node.attrs
            or node.attrs.get("aria-hidden", "").strip().lower() == "true"
            or "display:none" in style or "visibility:hidden" in style)


def _inline_visible_segments(node: _Node) -> tuple[str, ...]:
    """Bounded ephemeral label segments; never join across a block boundary."""
    def chunks(current: _Node, depth: int):
        if depth > 12 or _hidden(current):
            # A skipped subtree cannot glue unrelated visible text together.
            yield None
            return
        for child in current.children:
            if isinstance(child, str):
                yield child
            elif isinstance(child, _Node) and child.tag in _INLINE:
                yield from chunks(child, depth + 1)
            else:
                yield None

    segments: list[str] = []
    parts: list[str] = []
    remaining = 512
    for chunk in chunks(node, 0):
        if remaining <= 0:
            break
        if chunk is None:
            if parts:
                segments.append(" ".join(" ".join(parts).split()))
                parts.clear()
            continue
        fragment = chunk[:remaining]
        parts.append(fragment)
        remaining -= len(fragment)
    if parts:
        segments.append(" ".join(" ".join(parts).split()))
    return tuple(segments)


def _matches_label(node: _Node, pattern, *, confirmation: bool = False) -> bool:
    for label in _inline_visible_segments(node):
        if confirmation:
            label = re.sub(r"l([’'])\s+emprunt", r"l\1emprunt",
                           label, flags=re.IGNORECASE)
        if pattern.search(label):
            return True
    return False

def _empty(page: str, status: str = "ok") -> dict[str, object]:
    # Fixed keys and enumerated values only, never user-supplied input.
    return {
        "schema": SCHEMA, "page": page, "status": status,
        "quota_label": {"count": 0, "tag_paths": []},
        "pass_label": {"count": 0, "tag_paths": []},
        "copies_label": {"count": 0, "tag_paths": []},
        "confirmation_label": {"count": 0, "tag_paths": []},
        "book_scopes": 0, "unavailable_markers": 0,
        "borrow_controls": 0, "post_forms": 0,
        "csrf_field_names": 0, "resource_field_names": 0,
        "identity_bound": False, "quota_verified": False,
        "offer_verified": False, "borrow_permission_verified": False,
        "authorized": False, "eligibility": "unverified",
        **({"activity_shape": "unknown", "activity_verified": False}
           if page == "activity" else {}),
    }


def _count(value: int) -> int:
    """0/1/2 for zero, one, many; no counts of personal loans are exposed."""
    return min(2, value)


def _walk(node: _Node, path: tuple[str, ...] = ()):
    if _hidden(node):
        return
    tag = node.tag if node.tag in _SAFE_TAGS else "other"
    # The synthetic parser root is not a real HTML element.
    current = path if node.tag == "root" else (*path[-6:], tag)
    yield node, current
    for child in node.children:
        if isinstance(child, _Node):
            yield from _walk(child, current)


def _hit(report: dict, key: str, path: tuple[str, ...]):
    target = report[key]
    if target["count"] < 2:
        target["count"] += 1
        # Whitelisted HTML tag names only; never classes, ids, values,
        # freeform attributes, account names, loan counts or text.
        target["tag_paths"].append("/".join(path))


def qualify_bnf_borrow_dom(html: str, *, page: str) -> dict[str, object]:
    """Return a redacted, non-authorizing diagnostic from private HTML.

    Page labels are chosen by the local operator; they are not authenticated.
    The summary intentionally contains no positive rights, even when a label
    looks promising. All source data stays process-local in RAM.
    """
    if type(page) is not str or page not in PAGES:
        return _empty("unknown", "unsupported_page")
    report = _empty(page)
    if type(html) is not str:
        return _empty(page, "invalid_input")
    if len(html) > MAX_HTML_BYTES:
        return _empty(page, "limit")
    try:
        root = _dom(html)
    except (WebParseError, UnicodeError, ValueError, RecursionError):
        return _empty(page, "unrecognized")

    if page == "activity":
        # Structural rehearsal only. A locally supplied HTML document has
        # no authenticated HTTP status, origin or owner attestation. The
        # dummy scope is never exported or passed to Core/rights storage.
        dummy = ProfileScope("offline", "offline", "offline")
        try:
            snapshot = parse_loans(html, page_url=ORIGIN + "/my_profile/activity",
                                   expected=dummy, observed=dummy)
        except (WebParseError, UnicodeError, ValueError, RecursionError):
            return _empty(page, "unrecognized")
        if not snapshot.exhaustive:
            report["activity_shape"] = "partial"
        elif snapshot.loans:
            report["activity_shape"] = "nonempty_candidate"
        else:
            report["activity_shape"] = "empty_candidate"
        # Keep the inventory shape independent of the account label hints.
        # Both remain structural, not an authenticated account observation.

    visited = list(_walk(root))
    books = [node for node, _ in visited
             if "itemscope" in node.attrs
             and any(token in {
                 "https://schema.org/Book", "http://schema.org/Book",
             } for token in node.attrs.get("itemtype", "").split())]
    if page != "activity":
        report["book_scopes"] = _count(len(books))

    if page == "detail" and (len(books) != 1 or not books[0].closed):
        return _empty(page, "book_scope_unrecognized")
    scope_nodes = list(_walk(books[0])) if page == "detail" else visited
    for node, path in scope_nodes:
        if page == "detail":
            if node.has_class("lb_unavailable"):
                report["unavailable_markers"] = _count(
                    report["unavailable_markers"] + 1)
            if node.has_class("button-borrow"):
                report["borrow_controls"] = _count(
                    report["borrow_controls"] + 1)
        if page == "confirmation" and node.tag == "form":
            if node.closed and node.attrs.get("method", "").casefold() == "post":
                report["post_forms"] = _count(report["post_forms"] + 1)
        if page == "confirmation" and node.tag == "input":
            # Only compare known field *names*, never read or output values.
            name = node.attrs.get("name", "")
            if name == "authenticity_token":
                report["csrf_field_names"] = _count(
                    report["csrf_field_names"] + 1)
            elif name in ("resource_id", "resource", "book_id"):
                report["resource_field_names"] = _count(
                    report["resource_field_names"] + 1)
        patterns = (
            (("quota_label", _QUOTA), ("pass_label", _PASS))
            if page in ("profile", "activity") else
            (("copies_label", _COPIES),) if page == "detail" else
            (("confirmation_label", _CONFIRM),)
        )
        # A parent and its formatted child can carry the same phrase. Count
        # the deepest matching label, not both. An outer block cannot join
        # unrelated child paragraphs into a fabricated positive marker.
        confirmation = page == "confirmation"
        for key, pattern in patterns:
            if not _matches_label(node, pattern, confirmation=confirmation):
                continue
            if any(
                isinstance(child, _Node)
                and _matches_label(child, pattern, confirmation=confirmation)
                for child in node.children
            ):
                continue
            _hit(report, key, path)
    return report


__all__ = ["SCHEMA", "PAGES", "qualify_bnf_borrow_dom"]
