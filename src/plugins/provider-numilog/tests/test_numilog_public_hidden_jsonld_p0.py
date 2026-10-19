"""#1886 — only active, unambiguous public Book JSON-LD supplies evidence."""
import pytest

from cifereca_numilog_public_detail import _Scripts, PublicDetailError, parse_public_detail_jsonld

URL = "https://www.numilog.com/1958476/jefferson-prend-des-risques.ebook"
VISIBLE = ('<script type="application/ld+json">'
           '{"@type":"Book","url":"' + URL + '","name":"Jefferson prend des risques"}'
           '</script>')


@pytest.mark.parametrize("wrapper", ["template", "noscript", "iframe", "svg"])
def test_inert_wrappers_cannot_supply_a_product_jsonld(wrapper):
    hidden = "<" + wrapper + ">" + VISIBLE + "</" + wrapper + ">"
    reader = _Scripts()
    reader.feed(hidden + VISIBLE)
    reader.close()
    assert len(reader.scripts) == 1
    assert "Jefferson" in reader.scripts[0]
    with pytest.raises(PublicDetailError, match="missing_or_ambiguous_book"):
        parse_public_detail_jsonld(hidden, URL)


def test_visible_jsonld_still_considered_when_hidden_duplicate_is_present():
    reader = _Scripts()
    reader.feed("<template>" + VISIBLE + "</template>" + VISIBLE)
    reader.close()
    assert len(reader.scripts) == 1
    result = parse_public_detail_jsonld("<template>" + VISIBLE + "</template>" + VISIBLE, URL)
    assert result["fields"]["title"] == "Jefferson prend des risques"
    assert result["owned"] is False and result["downloadable"] is False


def test_duplicate_jsonld_script_type_is_not_a_trusted_source():
    reader = _Scripts()
    with pytest.raises(PublicDetailError, match="ambiguous_jsonld_type"):
        reader.feed('<script type="text/plain" type="application/ld+json">{}</script>')
