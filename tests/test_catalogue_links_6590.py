"""#6590 catalogue URL mechanics do not confer supplier authority."""
import pytest

from cifereca.plugin_api import catalogue_next, catalogue_url_origin, resolve_catalogue_link

BASE = "https://catalogue.example/opds/page"


def resolve(raw):
    return resolve_catalogue_link(BASE, raw, accept=lambda value:
                                  catalogue_url_origin(value) == catalogue_url_origin(BASE))


@pytest.mark.parametrize("raw", ("https://foreign.example/feed", "http://catalogue.example/feed",
                                "//catalogue.example:444/feed", "https://user@catalogue.example/feed",
                                "\\foreign.example", "https://catalogue.example/feed#part",
                                "/bad path", "/bad\x00path", ""))
def test_reject_navigation_outside_policy(raw):
    assert resolve(raw) is None


def test_policy_is_explicit_and_urls_are_not_fetched():
    assert resolve("../next?page=2") == "https://catalogue.example/next?page=2"
    assert resolve_catalogue_link(BASE, "/next", accept=lambda _: False) is None
    assert resolve_catalogue_link(BASE, "/next", accept=lambda _: 1) is None
    with pytest.raises(ValueError, match="invalid_catalogue_link_policy"):
        resolve_catalogue_link(BASE, "/next", accept=None)


def test_next_closed_states():
    assert catalogue_next([], resolve=resolve).state == "absent"
    next_page = catalogue_next(["/next", "https://catalogue.example/next"], resolve=resolve)
    assert (next_page.state, next_page.url) == ("valid", "https://catalogue.example/next")
    assert catalogue_next(["/next", "/other"], resolve=resolve).state == "ambiguous"
    invalid = catalogue_next(["/next", "https://foreign.example"], resolve=resolve)
    assert (invalid.state, invalid.url) == ("invalid", None)
