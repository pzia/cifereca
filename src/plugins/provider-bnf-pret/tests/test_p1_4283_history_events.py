"""Synthetic history DOM derived from the redacted #4283 handoff only."""
from dataclasses import replace

import pytest

from cifereca_bnf_pret_web import (
    ORIGIN, ProfileScope, WebParseError, parse_history_events,
)

SCOPE = ProfileScope("identity-test", "account-test", "epoch-test")
URL = ORIGIN + "/my_profile/history"
RID = "000000000000000000000001"
STAMP = "2020-01-01T08:00:00+01:00"


def card(rid=RID, stamp=STAMP, title="Titre fictif", kind="Prêt"):
    time = f'<time datetime="{stamp}">1 janvier 2020</time>' if stamp else ""
    return (
        f'<li class="input-group"><a href="{ORIGIN}/resources/{rid}">'
        '<img class="cover" src="capture_files/cover.jpg">'
        f'<h2 class="line-desc-title"><span class="line-desc-title__suptitle">{kind}</span> {title}</h2>'
        f'<p class="line-desc-supinfo">Emprunté le {time}</p></a></li>'
    )


def page(*cards, count=None, extra=""):
    count = len(cards) if count is None else count
    return (
        '<html lang="fr"><body id="members" class="profile_history" data-coffee-cup="Members#ProfileHistory">'
        '<nav><a class="nav-account__link-account" href="#account-menu">Compte test</a></nav>'
        '<div id="main"><aside><a class="aside-nav__current" href="/my_profile/history">Historique</a></aside>'
        '<header><h1 class="list-header__title">Historique</h1>'
        f'<p class="list-header__page"><strong>{count}</strong> résultats</p></header>'
        '<ul class="list-ui">' + "".join(cards) + '</ul>' + extra + '</div></body></html>'
    )


def parse(html, *, url=URL, observed=SCOPE):
    return parse_history_events(html, page_url=url, expected=SCOPE, observed=observed)


def test_one_observed_event_is_not_finished_or_exhaustive():
    result = parse(page(card()))
    event, = result.events
    assert (event.resource_id, event.title, event.borrowed_at) == (
        RID, "Titre fictif", "2020-01-01T07:00:00+00:00",
    )
    assert result.scope == SCOPE and result.displayed_count == 1
    assert result.status == "observed" and not result.exhaustive
    assert not hasattr(event, "loan_id") and not hasattr(event, "returned_at")
    assert RID not in repr(result) and "Compte test" not in repr(result)


def test_distinct_borrowing_events_of_same_resource_are_retained():
    result = parse(page(card(), card(stamp="2021-01-01T08:00:00+01:00")))
    assert len(result.events) == 2
    assert result.status == "observed" and not result.exhaustive


@pytest.mark.parametrize("stamp", ["", "2020-01-01", "2020-01-01T08:00:00", "2020-02-30T08:00:00Z"])
def test_absent_or_invalid_date_is_partial(stamp):
    result = parse(page(card(stamp=stamp)))
    assert result.events[0].borrowed_at is None
    assert result.status == "partial" and not result.exhaustive


def test_duplicate_and_conflicting_events_never_prove_count():
    same = parse(page(card(), card(stamp="2020-01-01T07:00:00Z")))
    assert len(same.events) == 1 and same.status == "partial"
    conflict = parse(page(card(), card(title="Autre titre"), card()))
    assert not conflict.events and conflict.status == "partial"


@pytest.mark.parametrize("html", [
    page(), page(card(), count=3), page(card(), count="inconnu"),
    page(card(), extra='<a rel="next" href="?page=2">Suite</a>'),
    page(card().replace('<li ', '<li hidden ')),
    page(card(kind="Réservation")),
    page(card().replace(ORIGIN, "https://invalid.example")),
])
def test_hypothetical_zero_pagination_and_invalid_cards_are_partial(html):
    result = parse(html)
    assert result.status == "partial" and not result.exhaustive


@pytest.mark.parametrize("html", [
    page(card()).replace("profile_history", "profile_activity"),
    page(card()).replace("Historique</h1>", "Activité</h1>"),
    page(card()).replace("nav-account__link-account", "login"),
    page(card()) + '<input type="password">',
    page(card()).replace("</ul>", ""),
    "<html><body>Login</body></html>",
])
def test_wrong_or_truncated_structure_is_not_empty(html):
    with pytest.raises(WebParseError):
        parse(html)


@pytest.mark.parametrize("url", [
    ORIGIN + "/my_profile/activity", ORIGIN + "/my_profile/history?l=en",
    "https://invalid.example/my_profile/history",
])
def test_unqualified_url_is_refused(url):
    with pytest.raises(WebParseError, match="invalid_history_page"):
        parse(page(card()), url=url)


@pytest.mark.parametrize("field", ["identity_id", "account_id", "session_epoch"])
def test_other_session_scope_is_refused(field):
    with pytest.raises(WebParseError, match="scope_mismatch"):
        parse(page(card()), observed=replace(SCOPE, **{field: "other"}))
