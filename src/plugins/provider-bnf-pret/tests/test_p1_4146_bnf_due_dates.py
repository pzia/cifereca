"""P1 #4146: dates officielles dans la seule carte de prêt BnF exacte.

All HTML and IDs are synthetic. No authentication, CDN, ACSM or mutation.
"""
import pytest

from bnf_token_fixture import connected
from cifereca_bnf_pret_web import ORIGIN, ProfileScope, WebParseError, parse_loans
from cifereca.gui.loan_view import loan_due_presentation, loan_due_detail_value


def card(lid="a", rid="book-a", value="01 janvier 2099 à 12:34",
         *, label="Votre prêt expirera le", extra="", attrs=""):
    due = (f'<div class="details-content__body"><div class="time-remaining" {attrs}>'
           f'{label} <strong>{value}</strong>.</div></div>') if value is not None else ""
    return (f'<li class="list-details__item js-loan" id="loan-{lid}">'
            '<article class="view-details js-loan__content">'
            f'<a href="/resources/{rid}">Livre fictif {rid}</a>'
            + due + extra + f'<a href="/loans/{lid}">Lecture</a>'
            '</article></li>')


def activity(*cards, outside=""):
    return (outside + '<section id="active-loans" class="resources active-loans">'
            '<ul class="list-details">' + "".join(cards)
            + '</ul></section>')


SCOPE = ProfileScope("identity-a", "account-a", "epoch-a")


def read(html, *, expected=SCOPE, observed=SCOPE):
    return parse_loans(html, page_url=ORIGIN + "/my_profile/activity",
                       expected=expected, observed=observed)


def test_four_distinct_attested_dates_and_quota_renewal_is_ignored():
    dates = ("01 janvier 2099 à 12:34", "28 février 2099 à 07:05",
             "30 juin 2099 à 23:59", "31 décembre 2099 à 00:00")
    snap = read(activity(*(card(str(i), f"edition-{i}", value=datum)
                           for i, datum in enumerate(dates)),
                         outside='<p>Vous serez de nouveau en mesure d\'emprunter '
                                 '<strong>02 janvier 2099 à 09:09</strong>.</p>'))
    assert snap.status == "observed"
    assert snap.exhaustive is True
    assert [x.due_at for x in snap.loans] == [
        "2099-01-01T12:34:00", "2099-02-28T07:05:00",
        "2099-06-30T23:59:00", "2099-12-31T00:00:00",
    ]
    assert len({x.resource_id for x in snap.loans}) == 4


def test_missing_date_keeps_positive_loan_and_inventory_proof():
    snap = read(activity(card("one", "one", value=None)))
    assert len(snap.loans) == 1
    assert snap.loans[0].due_at is None
    assert snap.exhaustive is True


@pytest.mark.parametrize("value", (
    "31 février 2099 à 12:34", "01 janvier 2099 à 24:01",
    "01 janvier 2099 à 12:60", "00 janvier 2099 à 12:34",
    "01 Janvierr 2099 à 12:34", "01 janvier 2099",
    "01 janvier 2099 à 12:34 UTC", "01 janvier 2099 à 12:34+01:00",
    "01 janvier 2099 à 12:34 02 janvier 2099 à 13:45",
))
def test_invalid_or_ambiguous_date_never_generates_a_due(value):
    snap = read(activity(card(value=value)))
    assert snap.loans[0].due_at is None


def test_french_accents_spacing_and_actual_leap_day():
    snap = read(activity(card(value="29 février 2096 à 08:06")))
    assert snap.loans[0].due_at == "2096-02-29T08:06:00"
    snap = read(activity(card(value="01 août 2099 à 08:06",
                              label="VOTRE PRÊT EXPIRERA LE")))
    assert snap.loans[0].due_at == "2099-08-01T08:06:00"


def test_neighbour_date_cannot_fill_missing_card():
    snap = read(activity(card("first", "book-1", value=None),
                         card("second", "book-2", value="28 mars 2099 à 18:09")))
    assert [row.due_at for row in snap.loans] == [
        None, "2099-03-28T18:09:00"]


def test_unrelated_nested_articles_cannot_supply_expiry():
    foreign = ('<article itemscope itemtype="https://schema.org/Book">'
               '<div class="details-content__body"><div class="time-remaining">'
               'Votre prêt expirera le <strong>01 janvier 2099 à 12:34</strong>.'
               '</div></div></article>')
    snap = read(activity(card(value=None, extra=foreign)))
    assert snap.loans[0].due_at is None


@pytest.mark.parametrize("kwargs", (
    {"label": "Votre quota expirera le"},
    {"attrs": "hidden"},
    {"attrs": 'aria-hidden="true"'},
    {"extra": '<div class="details-content__body"><div class="time-remaining">'
              'Votre prêt expirera le <strong>02 janvier 2099 à 12:34</strong>.'
              '</div></div>'},
))
def test_changed_label_hidden_or_multiple_blocks_stay_unknown(kwargs):
    snap = read(activity(card(**kwargs)))
    assert snap.loans[0].due_at is None


def test_duplicate_card_dates_conflict_without_erasing_other_loan():
    snap = read(activity(card("same", "book-a", value="01 janvier 2099 à 12:34"),
                         card("same", "book-a", value="02 janvier 2099 à 12:34"),
                         card("same", "book-a", value="01 janvier 2099 à 12:34"),
                         card("other", "book-b", value="03 janvier 2099 à 12:34")))
    assert len(snap.loans) == 2
    rows = {row.loan_id: row for row in snap.loans}
    assert rows["same"].due_at is None
    assert rows["other"].due_at == "2099-01-03T12:34:00"
    assert not snap.exhaustive


def test_different_identity_is_rejected_before_any_projection():
    html = activity(card())
    with pytest.raises(WebParseError, match="scope_mismatch"):
        read(html, observed=ProfileScope("identity-b", "account-a", "epoch-a"))


def test_session_list_projection_and_repeat_read_do_not_trigger_mutations():
    session, account, _, state, calls, downloaded = connected()
    try:
        before = len(calls)
        state["activity"] = activity(card("loan-1", "edition-1",
                                          value="01 janvier 2099 à 12:34"),
                                      card("loan-2", "edition-2",
                                          value="28 novembre 2099 à 07:05"))
        first = session.loans(account)
        second = session.loans(account)
        assert {x.id: x.due_at for x in first} == {
            "loan-1": "2099-01-01T12:34:00",
            "loan-2": "2099-11-28T07:05:00",
        }
        assert [x.due_at for x in first] == [x.due_at for x in second]
        assert all(x.state == "active" for x in first)
        assert downloaded == []
        assert all(method == "GET" and url == "/my_profile/activity"
                   for method, _host, url in calls[before:])
    finally:
        session.close()


def test_generic_list_and_detail_present_actual_due_date_without_bnf_ui_branch():
    from datetime import date
    session, account, _, state, _calls, _downloaded = connected()
    try:
        state["activity"] = activity(card(value="01 janvier 2099 à 12:34"))
        row, = session.loans(account)
        presentation = loan_due_presentation(row.due_at, today=date(2098, 12, 31))
        assert presentation.label == "Retour demain"
        detail = loan_due_detail_value(row.due_at)
        assert "2099" in detail
        assert detail != "Échéance inconnue"
    finally:
        session.close()


def test_second_session_cannot_supply_first_sessions_date():
    first, a1, _, s1, _, _ = connected()
    second, a2, _, s2, _, _ = connected()
    try:
        s1["activity"] = activity(card(value="01 janvier 2099 à 12:34"))
        s2["activity"] = activity(card(value=None))
        assert first.loans(a1)[0].due_at == "2099-01-01T12:34:00"
        assert second.loans(a2)[0].due_at is None
    finally:
        first.close()
        second.close()
