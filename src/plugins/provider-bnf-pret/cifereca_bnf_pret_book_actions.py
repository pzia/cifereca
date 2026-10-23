"""Diagnostic-only BnF book controls; NEVER an authorization to borrow.

The page-dependent signal is intentionally *weaker* than availability or
eligibility. It captures only bounded selector counts, not form methods,
action URLs, CSRF, identities, titles, disabled values or event handlers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from cifereca_bnf_pret_web import ProfileScope, WebParseError


BOOK_ACTIONS_JS = r"""() => {
  try {
    const books = document.querySelectorAll(
      '[itemscope][itemtype~="https://schema.org/Book"], ' +
      '[itemscope][itemtype~="http://schema.org/Book"]');
    if (books.length !== 1) return {code:'unrecognized_dom'};
    const count = selector => Math.min(books[0].querySelectorAll(selector).length, 101);
    return {
      code: 'ok',
      borrow_controls: count('.button-borrow'),
      booking_controls: count('.button-booking'),
      unavailable_markers: count('.lb_unavailable')
    };
  } catch (_) {
    return {code:'unrecognized_dom'};
  }
}"""


@dataclass(frozen=True, repr=False)
class BookActionSignals:
    scope: ProfileScope = field(repr=False)
    borrow_controls: int
    booking_controls: int
    unavailable_markers: int

    def __repr__(self):
        return "BookActionSignals(diagnostic_only=True)"

    @classmethod
    def from_browser(cls, payload, *, scope: ProfileScope):
        if not isinstance(scope, ProfileScope):
            raise WebParseError("scope_mismatch")
        if type(payload) is not dict or set(payload) != {
                "code", "borrow_controls", "booking_controls", "unavailable_markers"}:
            raise WebParseError("unrecognized_dom")
        if payload.get("code") != "ok":
            raise WebParseError("unrecognized_dom")
        values = tuple(payload[k] for k in (
            "borrow_controls", "booking_controls", "unavailable_markers"
        ))
        if any(type(x) is not int or not 0 <= x <= 101 for x in values):
            raise WebParseError("unrecognized_dom")
        return cls(scope, *values)

    @property
    def borrow_authorized(self) -> bool:
        """Never infer a permitted mutation from presentation-only controls."""
        return False
