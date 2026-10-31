"""#2797: six independently scoped purchased readBook dwld offers.

Pure HTML contract tests: no network, no signed URLs or user data.
"""
import pytest

from cifereca_numilog_html import parse_library_page, ParseError


def _card(lid, *, mode="dwld", title=True, extra=""):
    heading = f'<span class="pTitle">Fictional book {lid}</span>' if title else ""
    return (
        '<div class="owned-book">'
        + heading
        + f'<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid={lid}&amp;m={mode}">Download</a>'
        + extra
        + '</div>'
    )


def _page(*cards, outside=""):
    return '<html><table id="myTable">' + ''.join(cards) + '</table>' + outside + '</html>'


def test_six_purchases_each_have_one_exact_selected_dwld_offer():
    html = _page(*(_card(i) for i in range(1, 7)))
    diagnostics = {}
    rows, next_url = parse_library_page(
        html, delivery_evidence=True,
        delivery_selector=lambda row: row["lid"] == "4",
        delivery_diagnostics=diagnostics,
    )
    assert next_url is None
    assert len(rows) == 6
    assert [row["lid"] for row in rows] == [str(i) for i in range(1, 7)]
    assert [row.get("readbook_dwld_offer") is True for row in rows] == [
        False, False, False, True, False, False,
    ]
    assert diagnostics["cards"] == 6
    assert diagnostics["readbook_dwld_offers"] == 1
    assert diagnostics["bound_links"] == 0


@pytest.mark.parametrize("mode", ["stream", "download", "preview"])
def test_unattested_readbook_modes_never_become_offers(mode):
    rows, _ = parse_library_page(_page(_card(1, mode=mode)), delivery_evidence=True)
    assert len(rows) == 1
    assert "readbook_dwld_offer" not in rows[0]


def test_unscoped_js_object_does_not_qualify_another_book():
    outside = (
        '<script>window.offer = {ProductId: 1, IndexExemplaire: 1, '
        'DrmTechnlology: "ADOBE", link: "https://example.invalid/a.acsm"};</script>'
    )
    rows, _ = parse_library_page(
        _page(_card(1), _card(2), outside=outside),
        delivery_evidence=True,
        delivery_selector=lambda row: row["lid"] == "1",
    )
    assert rows[0].get("readbook_dwld_offer") is True
    assert rows[1].get("readbook_dwld_offer") is None
    assert rows[0].get("delivery") is None


def test_card_with_multiple_titles_never_attests_selected_delivery():
    html = _page(
        _card(1, extra='<span class="pTitle">Conflicting edition</span>'),
    )
    with pytest.raises(ParseError, match="Titres ambigus"):
        parse_library_page(html, delivery_evidence=True)
