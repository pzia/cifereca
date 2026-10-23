"""Bounded DOM selector counts; never inspect HTML, forms, text or URLs.

A count is diagnostic-only: not a login, rights or inventory proof.
"""
from dataclasses import dataclass, field
from cifereca_bnf_pret_web import ProfileScope, WebParseError

DOM_PROBE_JS = r"""() => {
  const count = selector => Math.min(document.querySelectorAll(selector).length, 101);
  try {
    return {
      code: 'ok',
      loan_sections: count('.active-loans'),
      loan_cards: count('.active-loans .js-loan'),
      search_sections: count('.search-results__list'),
      search_cards: count('.search-results__list .search-resource-result'),
      book_scopes: count('[itemscope][itemtype~="https://schema.org/Book"], [itemscope][itemtype~="http://schema.org/Book"]'),
      resource_links: count('a[href^="/resources/"]'),
      loan_links: count('a[href^="/loans/"]')
    };
  } catch (_) {
    return {code: 'unrecognized_dom'};
  }
}"""

_FIELDS = ("loan_sections", "loan_cards", "search_sections", "search_cards",
           "book_scopes", "resource_links", "loan_links")
_KINDS = frozenset({"activity", "results", "detail", "other"})


@dataclass(frozen=True, repr=False)
class DomProbe:
    scope: ProfileScope = field(repr=False)
    kind: str
    loan_sections: int
    loan_cards: int
    search_sections: int
    search_cards: int
    book_scopes: int
    resource_links: int
    loan_links: int

    def __repr__(self):
        return "DomProbe(diagnostic_only=True)"

    @classmethod
    def from_browser(cls, result, *, scope, kind):
        if not isinstance(scope, ProfileScope):
            raise WebParseError("scope_mismatch")
        if kind not in _KINDS or type(result) is not dict:
            raise WebParseError("unrecognized_dom")
        if set(result) != {"code", *_FIELDS} or result.get("code") != "ok":
            raise WebParseError("unrecognized_dom")
        values = [result[name] for name in _FIELDS]
        if any(type(value) is not int or not 0 <= value <= 101 for value in values):
            raise WebParseError("unrecognized_dom")
        return cls(scope, kind, *values)
