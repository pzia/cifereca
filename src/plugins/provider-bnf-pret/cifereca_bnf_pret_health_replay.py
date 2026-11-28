"""Thin adapter from the optional generic replay ABI to the existing parser."""
from cifereca_bnf_pret_web import ProfileScope, parse_loans, MAX_HTML_BYTES

_ACTIVITY = "https://pret.bnf.fr/my_profile/activity"


def parse_projection(capability, read):
    if capability != "loans":
        return None
    response = read("GET", _ACTIVITY)
    if (response.status_code != 200 or len(response.content) > MAX_HTML_BYTES
            or response.headers.get("content-type", "").split(";")[0].strip().lower()
            not in ("text/html", "application/xhtml+xml")):
        return None
    # Core has already checked exact archived scope. This is only an inert
    # parser input, never a new authentication or live session assertion.
    scope = ProfileScope("archived-account", "archived-account", "offline")
    snapshot = parse_loans(response.content.decode(response.encoding or "utf-8", errors="strict"),
                           page_url=_ACTIVITY, expected=scope, observed=scope)
    if not snapshot.exhaustive or snapshot.status != "observed":
        return None
    return tuple((row.loan_id, row.resource_id, row.title or "Prêt BnF",
                  ", ".join(row.authors)) for row in snapshot.loans)
