"""Private, fresh HTML ACSM action. Never a public Loan field or cached URL."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

from cifereca_bnf_pret_web import ORIGIN, _dom, _links


def signed_loan_url(href: str, loan_id: str) -> str | None:
    """Validate the observed five-field action without rewriting signed bytes."""
    if (type(href) is not str or not 0 < len(href) <= 4096
            or type(loan_id) is not str
            or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', loan_id) is None
            or any(ord(c) <= 32 or ord(c) > 126 or c in '\\#' for c in href)):
        return None
    try:
        parts = urlsplit(href)
        if (parts.scheme not in {'', 'https'}
                or parts.netloc not in {'', 'pret.bnf.fr'}
                or bool(parts.scheme) != bool(parts.netloc)
                or parts.path != '/loans/' + loan_id or not parts.query):
            return None
        # Reject malformed escapes and encoded/aliased parameter names.
        if re.search(r'%(?![0-9A-Fa-f]{2})', parts.query):
            return None
        names = [part.partition('=')[0] for part in parts.query.split('&')]
        expected = {'device_type', 'drm', 'nonce', 'timestamp', 'token'}
        if len(names) != 5 or set(names) != expected:
            return None
        pairs = parse_qsl(parts.query, keep_blank_values=True,
                          strict_parsing=True, max_num_fields=5,
                          encoding='utf-8', errors='strict')
        values = dict(pairs)
        if (len(values) != 5 or values['device_type'] != 'reader'
                or values['drm'] != 'acs4'
                or any(not value or len(value) > 2048
                       or any(ord(c) < 32 or ord(c) == 127 for c in value)
                       for value in values.values())):
            return None
        # Nonce, timestamp and token are opaque observed values. Do not
        # generate, parse/reformat, reorder or URL-encode them a second time.
        return href if parts.scheme else ORIGIN + href
    except (ValueError, UnicodeError):
        return None


def _signed_action_in_dom(root, loan_id: str, resource_id: str) -> str | None:
    """Use the same strict action validation on one already-parsed private DOM."""
    nodes = list(root.walk())
    candidates = [node for node in nodes if node.tag == 'a'
                  and node.attrs.get('aria-controls') == loan_id + '-reader-acs'
                  and all(node.has_class(name) for name in (
                      'actions-list__link', 'actions-list__sublink', 'js-accordeon__item'))]
    if not candidates:
        return None
    cards = [node for node in nodes if node.has_class('js-loan')]
    actions = set()
    for node in candidates:
        if not node.closed:
            return None
        # If a card owns this node, its identifiers must agree. An outside
        # accordion control is linked by its exact aria-controls AND href ID.
        for card in cards:
            if any(child is node for child in card.walk()):
                if (_links(card, 'loans') != {loan_id}
                        or _links(card, 'resources') != {resource_id}):
                    return None
        url = signed_loan_url(node.attrs.get('href', ''), loan_id)
        if url is None:
            return None
        actions.add(url)
    return next(iter(actions)) if len(actions) == 1 else None


def signed_acsm_action(html: str, loan_id: str, resource_id: str) -> str | None:
    """Find one signed ACSM action tied to the exact fresh loan/resource.

    An action outside .js-loan must match the loan's aria-controls and URL;
    an action under a foreign card is rejected. The URL stays transient and
    must never enter an inventory row, diagnosis or rights snapshot.
    """
    return _signed_action_in_dom(_dom(html), loan_id, resource_id)


def reader_acs4_offer_pairs(
    html: str, loan_resources: tuple[tuple[str, str], ...],
) -> frozenset[tuple[str, str]]:
    """Qualify *presence* of exact signed reader ACSM actions in one DOM parse.

    Returns only pairs already supplied by a validated active-loan parser:
    never the signed URL, token, cookie, or a catalogue-derived offer. An
    unrecognized or conflicting action cannot become an available offer.
    """
    root = _dom(html)
    active = [n for n in root.walk() if n.has_class('active-loans') and n.closed]
    if len(active) != 1:
        return frozenset()
    source_pairs: dict[str, set[str]] = {}
    for card in active[0].walk():
        if not card.has_class('js-loan') or not card.closed:
            continue
        loan_ids, resource_ids = _links(card, 'loans'), _links(card, 'resources')
        if len(loan_ids) == len(resource_ids) == 1:
            source_pairs.setdefault(next(iter(loan_ids)), set()).add(
                next(iter(resource_ids))
            )
    return frozenset(
        (loan_id, resource_id)
        for loan_id, resource_id in loan_resources
        if source_pairs.get(loan_id) == {resource_id}
        and _signed_action_in_dom(root, loan_id, resource_id) is not None
    )
