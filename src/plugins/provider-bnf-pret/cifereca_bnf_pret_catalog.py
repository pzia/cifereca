"""Read-only HTML catalogue traversal; never infer rights or completeness."""
from __future__ import annotations

import re
import time
from urllib.parse import urlencode
from cifereca_bnf_pret_web import (
    ORIGIN, _dom, parse_suggestions, parse_detail, WebParseError,
)
from cifereca_bnf_pret_paging import _params, observed_next_results_url
from cifereca_bnf_pret_http import ReaderHTTPError

MAX_SEARCH_PAGES = 5
MAX_SEARCH_RECORDS = 500


def query_url(query):
    if (type(query) is not str or not 2 <= len(query.strip()) <= 128
            or any(ord(c) < 32 or ord(c) == 127 for c in query)):
        raise WebParseError('invalid_query')
    return ORIGIN + '/results?' + urlencode({'q': query.strip()})


def resource_url(resource_id):
    if type(resource_id) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', resource_id):
        raise WebParseError('invalid_resource_page')
    return ORIGIN + '/resources/' + resource_id


def next_link(html, current_url):
    root = _dom(html)
    anchors = [n for n in root.walk() if n.tag == 'a' and n.closed
               and 'next' in n.attrs.get('rel', '').split()]
    for node in root.walk():
        if node.has_class('pagination') and node.closed:
            anchors.extend(n for n in node.walk() if n.tag == 'a' and n.closed and n.has_class('next'))
    targets = {n.attrs.get('href', '') for n in anchors}
    if not targets:
        return None
    if len(targets) != 1:
        raise WebParseError('next_page_unavailable')
    return observed_next_results_url(current_url, targets.pop())


def read_search(client, query, *, is_current=lambda: True):
    url = query_url(query)
    deadline = time.monotonic() + 30
    records = {}
    # No link absence, count or successful HTML parse attests exhaustiveness.
    warnings = ['inventory_not_exhaustive']
    for page_index in range(MAX_SEARCH_PAGES):
        try:
            if not is_current():
                raise ReaderHTTPError('session_closed')
            current, html = client.request('GET', url, deadline=deadline)
            if not is_current():
                raise ReaderHTTPError('session_closed')
            expected, actual = _params(url), _params(current)
            expected.setdefault('page', '1')
            actual.setdefault('page', '1')
            if expected != actual:
                raise WebParseError('search_redirect_mismatch')
            parsed = parse_suggestions(html)
            for record in parsed.records:
                previous = records.get(record.resource_id)
                if previous is not None and previous != record:
                    if 'ambiguous_search' not in warnings:
                        warnings.append('ambiguous_search')
                    continue
                records[record.resource_id] = record
            if parsed.status != 'ok':
                warnings.append('partial_search')
            if len(records) > MAX_SEARCH_RECORDS:
                raise WebParseError('item_limit')
            following = next_link(html, current)
            if following is None:
                break
            if page_index + 1 == MAX_SEARCH_PAGES:
                warnings.append('search_page_limit')
                break
            url = following
        except (ReaderHTTPError, WebParseError) as error:
            # A later page failure must retain earlier observed public records.
            # First-page failures must never masquerade as an empty catalogue.
            if not records:
                raise
            warnings.append(str(error))
            break
    return tuple(records.values()), tuple(dict.fromkeys(warnings))


def read_detail(client, resource_id):
    target = resource_url(resource_id)
    current, html = client.request('GET', target)
    if current != target:
        raise WebParseError('detail_redirect_mismatch')
    return parse_detail(html, page_url=current).records[0]
