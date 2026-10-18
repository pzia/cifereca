"""Opt-in, synthetic Numilog *commercial* search fixture (#771/#1886).

This is a simulation-only HTTP contract; it is neither the Numilog live API
nor a source of account rights. All products, prices and URLs are fictitious.
"""
from __future__ import annotations

from html import escape
from http import HTTPStatus
from urllib.parse import urlencode

SEARCH_PATH = "/Pages/Recherche/ResultatRecherche.aspx"
GRID_ID = "ctl02_CentrePage_ListeLivres1_GridView1"
_FAULTS = {"429", "503", "login_html", "bad_grid"}


def _one(qs, key, default=None):
    values = qs.get(key)
    if values is None:
        return default
    if len(values) != 1 or type(values[0]) is not str:
        raise ValueError("invalid public query")
    return values[0]


def render_public_search(cache, query_params, page_size):
    """Return (status, headers, bytes) without login/cookie/ACSM access.

    Faults are explicit and cannot leak into the personal-library routes.
    A finite local query exercises the real public HTML parser, not the site.
    """
    if set(query_params) - {"mode", "query", "GRID_PAGE", "ux_fault"}:
        raise ValueError("unsupported public parameter")
    mode = _one(query_params, "mode")
    query = _one(query_params, "query")
    page = _one(query_params, "GRID_PAGE", "0")
    fault = _one(query_params, "ux_fault", "")
    if (mode != "brSimple" or query is None or len(query) > 180
            or any(ord(char) < 32 or ord(char) == 127 for char in query)
            or not page.isascii() or not page.isdecimal() or len(page) > 3
            or (len(page) > 1 and page[0] == "0")
            or (fault and fault not in _FAULTS)):
        raise ValueError("invalid public query")
    index = int(page)
    if fault in {"429", "503"}:
        status = HTTPStatus.TOO_MANY_REQUESTS if fault == "429" else HTTPStatus.SERVICE_UNAVAILABLE
        headers = {"Retry-After": "60"} if fault == "429" else {}
        return status, headers, b"synthetic service unavailable"
    if fault == "login_html":
        return HTTPStatus.OK, {}, b"<html><title>Connexion fictive</title></html>"
    if fault == "bad_grid":
        return HTTPStatus.OK, {}, b"<html><span class='ResultatDetail'>1</span><table></table></html>"

    # This is intentionally a public subset of fabricated records. It does
    # not consult sessions, cookies, private manifests or signed delivery links.
    query_lower = query.casefold()
    rows = list(cache.connect().execute("SELECT * FROM books ORDER BY lid"))
    if query_lower:
        rows = [row for row in rows if query_lower in " ".join(
            str(row[field]) for field in ("title", "author", "publisher", "subject")
        ).casefold()]
    total = len(rows)
    subset = rows[index * page_size:(index + 1) * page_size]
    cards = []
    for row in subset:
        lid = int(row["lid"])
        # High synthetic IDs are NOT real Numilog products; no HTTP is ever
        # sent to these URLs by the test harness.
        product_id = 9000000000 + lid
        link = f"../../{product_id}/synthetic-book-{lid}.ebook"
        cards.append(
            '<tr class="LivreDuThemeDetail"><td>'
            f'<a class="TitleBook" href="{link}">{escape(str(row["title"]))}</a>'
            f'<a class="Author">{escape(str(row["author"]))}</a>'
            '<div class="product_meta">'
            f'<div class="LabelInfos">Éditeur : {escape(str(row["publisher"]))}</div>'
            f'<div class="LabelInfos">Parution : {escape(str(row["publication_date"]))}</div>'
            '<div class="LabelInfos">Format(s) : ePub</div></div>'
            '<div class="ColPrice">'
            '<span class="LblPrice">9</span>'
            '<span class="LblDecimal">99</span>'
            '<span class="LblSeparatorPrice">€</span>'
            '</div></td></tr>'
        )
    pager = ""
    if total > page_size:
        links = ""
        if (index + 1) * page_size < total:
            params = urlencode({"mode": "brSimple", "query": query, "GRID_PAGE": str(index + 1)})
            links = f'<a href="{escape(SEARCH_PATH + "?" + params, quote=True)}">Suivant</a>'
        pager = f'<nav class="pagination"><span class="PagerCurrentPage">{index + 1}</span>{links}</nav>'
    html = (
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'></head><body>"
        f'<span class="ResultatDetail">{total}</span>'
        f'<table id="{GRID_ID}"><tbody>{"".join(cards)}</tbody></table>'
        f'{pager}</body></html>'
    )
    return HTTPStatus.OK, {}, html.encode("utf-8")
