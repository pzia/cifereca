"""Numilog #1886: JSON-LD parser resource bounds are stable and fail closed."""
import pytest

from cifereca_numilog_public_detail import (
    MAX_JSONLD_BYTES, MAX_SCRIPTS, PublicDetailError, _Scripts,
    parse_public_detail_jsonld,
)

URL = "https://www.numilog.com/1958476/jefferson-prend-des-risques.ebook"


def html(data):
    return '<script type="application/ld+json">' + data + '</script>'


def test_oversized_ascii_jsonld_preserves_specific_error_code():
    with pytest.raises(PublicDetailError) as caught:
        parse_public_detail_jsonld(html("A" * (MAX_JSONLD_BYTES + 1)), URL)
    assert caught.value.code == "jsonld_too_large"
    assert len(str(caught.value)) < 100


def test_utf8_byte_limit_is_not_mistaken_for_character_limit():
    # The entire HTML is below the 1 MiB cap, but JSON-LD is over 128 KiB.
    with pytest.raises(PublicDetailError) as caught:
        parse_public_detail_jsonld(html("é" * (MAX_JSONLD_BYTES // 2 + 1)), URL)
    assert caught.value.code == "jsonld_too_large"


def test_fragmented_jsonld_accounting_is_incremental():
    parser = _Scripts()
    parser.handle_starttag("script", [("type", "application/ld+json")])
    for _ in range(256):
        parser.handle_data("x" * 512)
    assert parser.parts_bytes == MAX_JSONLD_BYTES
    assert len(parser.parts) == 256
    with pytest.raises(PublicDetailError) as caught:
        parser.handle_data("x")
    assert caught.value.code == "jsonld_too_large"


def test_jsonld_script_count_preserves_specific_error_code():
    scripts = html('{"@type":"Organization"}') * (MAX_SCRIPTS + 1)
    with pytest.raises(PublicDetailError) as caught:
        parse_public_detail_jsonld(scripts, URL)
    assert caught.value.code == "too_many_jsonld_scripts"


def test_malformed_jsonld_returns_fixed_code_without_payload():
    with pytest.raises(PublicDetailError) as caught:
        parse_public_detail_jsonld(html('PRIVATE_MARKER'), URL)
    assert caught.value.code == "invalid_jsonld"
    assert "PRIVATE_MARKER" not in str(caught.value)


def test_missing_book_has_separate_unambiguous_error():
    with pytest.raises(PublicDetailError) as caught:
        parse_public_detail_jsonld(html('{"@type":"Product"}'), URL)
    assert caught.value.code == "missing_or_ambiguous_book"


def test_html_parser_value_error_is_still_wrapped_without_leak(monkeypatch):
    def broken(_self, _text):
        raise ValueError("PRIVATE_MARKER")
    monkeypatch.setattr(_Scripts, "feed", broken)
    with pytest.raises(PublicDetailError) as caught:
        parse_public_detail_jsonld(html('{"@type":"Book"}'), URL)
    assert caught.value.code == "invalid_html"
    assert "PRIVATE_MARKER" not in str(caught.value)


def test_non_jsonld_scripts_do_not_count_against_book_limit():
    non_json = '<script>token="PRIVATE_MARKER"</script>' * (MAX_SCRIPTS + 3)
    with pytest.raises(PublicDetailError) as caught:
        parse_public_detail_jsonld(non_json + html('{"@type":"Product"}'), URL)
    assert caught.value.code == "missing_or_ambiguous_book"
    assert "PRIVATE_MARKER" not in str(caught.value)
