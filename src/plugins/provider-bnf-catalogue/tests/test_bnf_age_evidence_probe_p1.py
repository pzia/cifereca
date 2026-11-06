"""Offline SRU BnF MarcXchange exact-edition evidence contract (#4530)."""
import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "bnf_age_evidence_probe.py"
spec = importlib.util.spec_from_file_location("bnf_age_evidence_probe", PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

SRU = "http://www.loc.gov/zing/srw/"
MXC = "info:lc/xmlns/marcxchange-v2"
ISBN = "9782377314652"
PUBLIC_FIXTURES = Path(__file__).with_name("fixtures") / "bnf-age-sru"


def response(*records, total=None):
    count = len(records) if total is None else total
    body = "".join(records)
    return (f'<srw:searchRetrieveResponse xmlns:srw="{SRU}" xmlns:mxc="{MXC}">'
            f'<srw:numberOfRecords>{count}</srw:numberOfRecords>'
            f'<srw:records>{body}</srw:records></srw:searchRetrieveResponse>').encode()


def record(isbn=ISBN, fmt="UNIMARC", ages=None, ark="ark:/12148/cb465971480",
           extra_isbn=None, bib_type="Bibliographic"):
    isbntag = "010" if fmt == "UNIMARC" else "020"
    age_tag = "329" if fmt == "UNIMARC" else "833"
    age_rows = "".join(
        f'<mxc:datafield tag="{age_tag}"><mxc:subfield code="a">{value}</mxc:subfield>'
        f'</mxc:datafield>' for value in (ages or [])
    )
    extra = (f'<mxc:datafield tag="{isbntag}"><mxc:subfield code="a">'
             f'{extra_isbn}</mxc:subfield></mxc:datafield>') if extra_isbn else ""
    return (
        f'<srw:record><srw:recordSchema>marcxchange</srw:recordSchema>'
        f'<srw:recordPacking>xml</srw:recordPacking>'
        f'<srw:recordData><mxc:record id="{ark}" '
        f'format="{fmt}" type="{bib_type}">'
        f'<mxc:datafield tag="{isbntag}"><mxc:subfield code="a">'
        f'{isbn}</mxc:subfield></mxc:datafield>{extra}{age_rows}'
        f'</mxc:record></srw:recordData></srw:record>'
    )


def test_raw_unimarc_age_is_not_promoted_to_numeric_age():
    result = probe.exact_edition_age_fields(response(record(ages=["À partir de 13 ans"])), ISBN)
    assert result == [{
        "record_ark": "ark:/12148/cb465971480",
        "own_isbn13": ISBN,
        "marc_format": "UNIMARC",
        "field_tag": "329",
        "raw_subfields": (("a", "À partir de 13 ans"),),
        "evidence_role": "cnlj_critical_review_not_age",
        "age_status": "unqualified_raw_evidence",
    }]
    assert not any("min_age" in value or "max_age" in value for value in result)


def test_intermediate_age_833_and_isbn10_conversion():
    assert probe.canonical_isbn("2-37731-465-0") is None
    other = "0306406152"
    converted = probe.canonical_isbn(other)
    assert converted == "9780306406157"
    data = response(record(isbn=other, fmt="INTERMARC", ages=["9+"]))
    assert probe.exact_edition_age_fields(data, converted)[0]["field_tag"] == "833"
    assert probe.exact_edition_age_fields(data, converted)[0]["evidence_role"] == "cnlj_mixed_review_or_audience"


@pytest.mark.parametrize("field_isbn", [None, "9782377314653", "0000000000000"])
def test_unmatched_or_invalid_isbn_is_not_proof(field_isbn):
    if field_isbn is None:
        xml = response(record(ages=["13 ans"]).replace(
            '<mxc:datafield tag="010"><mxc:subfield code="a">' + ISBN
            + '</mxc:subfield></mxc:datafield>', ""))
    else:
        xml = response(record(isbn=field_isbn, ages=["13 ans"]))
    assert probe.exact_edition_age_fields(xml, ISBN) == []


def test_ambiguous_own_isbns_and_related_editions_are_excluded():
    assert probe.exact_edition_age_fields(
        response(record(extra_isbn="9780306406157", ages=["13 ans"])), ISBN) == []
    assert probe.exact_edition_age_fields(
        response(record(isbn="9780306406157", ages=["13 ans"])), ISBN) == []


def test_missing_age_or_authority_record_is_no_evidence():
    assert probe.exact_edition_age_fields(response(record()), ISBN) == []
    assert probe.exact_edition_age_fields(
        response(record(ages=["13 ans"], bib_type="Authority")), ISBN) == []


@pytest.mark.parametrize("xml", [
    b"<root/>", b"<srw:searchRetrieveResponse/>",
    b"<!DOCTYPE root [<!ENTITY x SYSTEM 'file:///etc/passwd'>]><root/>",
    b"not xml", b"a" * (probe.MAX_XML_BYTES + 1),
])
def test_invalid_responses_fail_closed(xml):
    with pytest.raises(ValueError):
        probe.exact_edition_age_fields(xml, ISBN)


def test_record_limit_and_no_http_import():
    with pytest.raises(ValueError, match="sru_record_limit"):
        probe.exact_edition_age_fields(response(*(record() for _ in range(51))), ISBN)
    source = PATH.read_text(encoding="utf-8")
    assert "httpx" not in source and "requests" not in source


def test_bnf_sru_wrapper_schema_and_packing_are_required():
    good = record(ages=["13 ans"])
    assert probe.exact_edition_age_fields(response(good), ISBN)
    assert probe.exact_edition_age_fields(response(
        good.replace("<srw:recordSchema>marcxchange</srw:recordSchema>",
                     "<srw:recordSchema>dublincore</srw:recordSchema>")), ISBN) == []
    assert probe.exact_edition_age_fields(response(
        good.replace("<srw:recordPacking>xml</srw:recordPacking>",
                     "<srw:recordPacking>string</srw:recordPacking>")), ISBN) == []


@pytest.mark.parametrize("count", ["", "-1", "abc", "2x", "0"])
def test_sru_count_is_not_inferred_from_wrapped_records(count):
    with pytest.raises(ValueError):
        probe.exact_edition_age_fields(response(record(ages=["13 ans"]), total=count), ISBN)


def test_contradictory_or_unknown_marc_subfield_is_not_interpreted():
    source = record(ages=["13 ans"])
    tampered = source.replace('<mxc:subfield code="a">13 ans</mxc:subfield>',
                              '<mxc:subfield code="z">JAg1399</mxc:subfield>')
    result = probe.exact_edition_age_fields(response(tampered), ISBN)
    assert result[0]["raw_subfields"] == (("z", "JAg1399"),)
    assert result[0]["age_status"] == "unqualified_raw_evidence"


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be", "utf-32"])
def test_other_xml_encodings_cannot_bypass_entity_rejection(encoding):
    payload = response(record(ages=["&audience;"])).decode("utf-8")
    payload = '<!DOCTYPE srw:searchRetrieveResponse [<!ENTITY audience "Synthetic">]>' + payload
    with pytest.raises(ValueError, match="invalid_sru_encoding"):
        probe.exact_edition_age_fields(payload.encode(encoding), ISBN)


@pytest.mark.parametrize(("name", "isbn", "tag", "pair", "count"), [
    ("age-tendre", ISBN, "333", ("a", "À partir de 13 ans"), 1),
    ("age-tendre-intermarc", ISBN, "833", ("m", "JAg1399"), 2),
    ("carre-unimarc", "9791040120971", "333", ("a", "De 0 à 3 ans"), 1),
    ("carre-intermarc", "9791040120971", "833", ("m", "JAg0003"), 2),
    ("conflicting-intermarc", "9782362901645", "833", ("m", "JAg0699"), 2),
])
def test_public_sru_age_evidence_preserves_actual_fields(name, isbn, tag, pair, count):
    data = (PUBLIC_FIXTURES / f"{name}.xml").read_bytes()
    evidence = probe.exact_edition_age_fields(data, isbn)
    assert sum(row["field_tag"] == tag and pair in row["raw_subfields"]
               for row in evidence) == count


@pytest.mark.parametrize(("name", "isbn"), [
    ("baby", "9782324036668"), ("age-tendre-ebook", "9782377315123"), ("homonyms", ISBN),
])
def test_public_sru_responses_without_exact_age_do_not_supply_evidence(name, isbn):
    data = (PUBLIC_FIXTURES / f"{name}.xml").read_bytes()
    assert probe.exact_edition_age_fields(data, isbn) == []


def test_public_sru_record_processing_diagnostic_is_not_empty_evidence():
    data = (PUBLIC_FIXTURES / "conflicting.xml").read_bytes()
    with pytest.raises(ValueError, match="sru_diagnostics"):
        probe.exact_edition_age_fields(data, "9782362901645")


def test_unimarc_audience_note_333_is_distinct_from_cnlj_review_329():
    xml = response(record(ages=["critique"]) .replace(
        'tag="329"', 'tag="333"'))
    result = probe.exact_edition_age_fields(xml, ISBN)
    assert len(result) == 1
    assert result[0]["evidence_role"] == "audience_note_unqualified"
    assert result[0]["field_tag"] == "333"
    assert result[0]["age_status"] == "unqualified_raw_evidence"


@pytest.mark.parametrize(("name", "isbn", "minimum", "maximum", "field", "literal"), [
    ("age-tendre", ISBN, 13, None, "333", "À partir de 13 ans"),
    ("age-tendre-intermarc", ISBN, 13, None, "833", "JAg1399"),
    ("carre-unimarc", "9791040120971", 0, 3, "333", "De 0 à 3 ans"),
    ("carre-intermarc", "9791040120971", 0, 3, "833", "JAg0003"),
    ("conflicting-intermarc", "9782362901645", 6, None, "833", "JAg0699"),
])
def test_public_observed_cnlj_age_is_bounded_without_core_promotion(
    name, isbn, minimum, maximum, field, literal,
):
    result = probe.qualified_observed_age_fields(
        (PUBLIC_FIXTURES / (name + ".xml")).read_bytes(), isbn,
    )
    assert len(result) == 1
    claim = result[0]
    assert claim["own_isbn13"] == isbn
    assert claim["field_tag"] == field
    assert claim["source_literal"] == literal
    assert (claim["audience_age_min"], claim["audience_age_max"]) == (
        minimum, maximum,
    )
    assert claim["evidence_role"] == "cnlj_bibliographic_target_audience"
    assert claim["edition_support_verified"] is False
    assert claim["core_youth_age_range_authorized"] is False
    assert "age_min" not in claim and "age_max" not in claim


@pytest.mark.parametrize(("name", "isbn"), [
    ("baby", "9782324036668"),
    ("age-tendre-ebook", "9782377315123"),
    ("homonyms", ISBN),
])
def test_no_cnlj_claim_for_absent_field_or_distinct_digital_isbn(name, isbn):
    assert probe.qualified_observed_age_fields(
        (PUBLIC_FIXTURES / (name + ".xml")).read_bytes(), isbn,
    ) == []


def test_unimarc_329_critical_review_is_not_numerical_evidence():
    content = response(record(ages=["À partir de 13 ans"]))
    assert probe.exact_edition_age_fields(content, ISBN)
    assert probe.qualified_observed_age_fields(content, ISBN) == []


def test_cnlj_333_without_authority_is_not_an_age_assertion():
    original = (PUBLIC_FIXTURES / "age-tendre.xml").read_bytes()
    modified = original.replace(
        b'<mxc:subfield code="2">CNLJ</mxc:subfield>',
        b'<mxc:subfield code="2">Other</mxc:subfield>',
    )
    assert probe.qualified_observed_age_fields(modified, ISBN) == []


@pytest.mark.parametrize(("name", "old", "new"), [
    ("age-tendre-intermarc", b"JAg1399", b"JAg1398"),
    ("carre-intermarc", b"JAg0003", b"JAg0004"),
    ("age-tendre", "À partir de 13 ans".encode(), "Dès 13 ans".encode()),
])
def test_unknown_or_unqualified_age_shape_stays_raw_not_numeric(name, old, new):
    data = (PUBLIC_FIXTURES / (name + ".xml")).read_bytes()
    altered = data.replace(old, new)
    assert probe.exact_edition_age_fields(
        altered, ISBN if name != "carre-intermarc" else "9791040120971",
    )
    assert probe.qualified_observed_age_fields(
        altered, ISBN if name != "carre-intermarc" else "9791040120971",
    ) == []


def test_conflicting_cnlj_codes_on_one_record_are_not_reconciled():
    data = (PUBLIC_FIXTURES / "age-tendre-intermarc.xml").read_bytes()
    # Two genuine repeated 833$m claims are present; changing one produces
    # conflicting observed intervals (13+ versus 6+).
    altered = data.replace(b"JAg1399", b"JAg0699", 1)
    assert len(probe.exact_edition_age_fields(altered, ISBN)) == 2
    assert probe.qualified_observed_age_fields(altered, ISBN) == []


def test_invalid_sru_or_remote_processing_diagnostic_never_becomes_an_age():
    with pytest.raises(ValueError, match="sru_diagnostics"):
        probe.qualified_observed_age_fields(
            (PUBLIC_FIXTURES / "conflicting.xml").read_bytes(),
            "9782362901645",
        )
    with pytest.raises(ValueError, match="sru_entities_forbidden"):
        probe.qualified_observed_age_fields(
            b'<!DOCTYPE root [<!ENTITY x "x">]><root/>', ISBN,
        )


def test_age_qualifier_has_no_side_effectful_or_network_import():
    source = PATH.read_text(encoding="utf-8")
    assert "def qualified_observed_age_fields(" in source
    assert "httpx" not in source and "requests" not in source


def _synthetic_cnlj_333(ark="ark:/12148/cb465971480", age="À partir de 13 ans"):
    # Synthetic structural mutation, never described as a captured BnF page.
    raw = record(ark=ark, ages=[age]).replace('tag="329"', 'tag="333"')
    return raw.replace(
        "</mxc:datafield>",
        '<mxc:subfield code="2">CNLJ</mxc:subfield></mxc:datafield>',
        2,
    )


def test_same_own_isbn_on_two_different_arks_is_ambiguous():
    first = _synthetic_cnlj_333()
    second = _synthetic_cnlj_333(ark="ark:/12148/cb465971481")
    assert len(probe.exact_edition_age_fields(response(first, second), ISBN)) == 2
    assert probe.qualified_observed_age_fields(response(first, second), ISBN) == []


def test_conflicting_ark_with_a_different_age_cannot_choose_first():
    first = _synthetic_cnlj_333()
    second = _synthetic_cnlj_333(
        ark="ark:/12148/cb465971481", age="De 0 à 3 ans",
    )
    assert probe.qualified_observed_age_fields(response(first, second), ISBN) == []


def test_known_833_followed_by_unknown_833_is_not_partially_qualified():
    data = (PUBLIC_FIXTURES / "age-tendre-intermarc.xml").read_bytes()
    # The reference has two identical, safely deduplicated JAg1399 fields.
    tampered = data.replace(b"JAg1399", b"JAg1398", 1)
    assert len(probe.exact_edition_age_fields(tampered, ISBN)) == 2
    assert probe.qualified_observed_age_fields(tampered, ISBN) == []


def test_known_833_with_unqualified_extra_subfield_is_not_sufficient():
    data = (PUBLIC_FIXTURES / "age-tendre-intermarc.xml").read_bytes()
    tampered = data.replace(
        b"<mxc:subfield code=\"m\">JAg1399</mxc:subfield>",
        b"<mxc:subfield code=\"m\">JAg1399</mxc:subfield>"
        b"<mxc:subfield code=\"z\">unqualified context</mxc:subfield>",
        1,
    )
    assert len(probe.exact_edition_age_fields(tampered, ISBN)) == 2
    assert probe.qualified_observed_age_fields(tampered, ISBN) == []


def test_recognized_333_followed_by_333_without_cnlj_vetoes_projection():
    first = _synthetic_cnlj_333()
    # Second field asserts a different range but lacks the CNLJ authority.
    extra_field = ('<mxc:datafield tag="333"><mxc:subfield code="a">'
                   'De 0 à 3 ans</mxc:subfield></mxc:datafield>')
    modified = first.replace("</mxc:record>", extra_field + "</mxc:record>")
    assert len(probe.exact_edition_age_fields(response(modified), ISBN)) == 2
    assert probe.qualified_observed_age_fields(response(modified), ISBN) == []


def test_exact_observed_public_positive_fixtures_still_qualify_after_hardening():
    cases = (
        ("age-tendre", ISBN, 13, None),
        ("age-tendre-intermarc", ISBN, 13, None),
        ("carre-unimarc", "9791040120971", 0, 3),
        ("carre-intermarc", "9791040120971", 0, 3),
        ("conflicting-intermarc", "9782362901645", 6, None),
    )
    for filename, isbn, low, high in cases:
        rows = probe.qualified_observed_age_fields(
            (PUBLIC_FIXTURES / (filename + ".xml")).read_bytes(), isbn,
        )
        assert len(rows) == 1
        assert (rows[0]["audience_age_min"], rows[0]["audience_age_max"]) == (
            low, high,
        )
        assert rows[0]["core_youth_age_range_authorized"] is False


def test_secondary_own_isbn_record_without_age_still_vetoes_single_ark():
    first = _synthetic_cnlj_333()
    # The second exact-own-ISBN bibliographic record has no 333/833.
    # It must not disappear from the ambiguity check.
    secondary = record(ark="ark:/12148/cb465971481", ages=[])
    payload = response(first, secondary)
    assert len(probe.exact_edition_age_fields(payload, ISBN)) == 1
    assert probe.qualified_observed_age_fields(payload, ISBN) == []


def test_repeated_same_ark_without_audience_is_not_a_distinct_edition():
    first = _synthetic_cnlj_333()
    same_ark = record(ark="ark:/12148/cb465971480", ages=[])
    payload = response(first, same_ark)
    assert len(probe.exact_edition_age_fields(payload, ISBN)) == 1
    claims = probe.qualified_observed_age_fields(payload, ISBN)
    assert len(claims) == 1
    assert claims[0]["core_youth_age_range_authorized"] is False


def test_own_isbn_match_collector_includes_no_age_record_without_changing_raw():
    first = _synthetic_cnlj_333()
    secondary = record(ark="ark:/12148/cb465971481", ages=[])
    matched: set[str] = set()
    raw = probe.exact_edition_age_fields(
        response(first, secondary), ISBN, _matched_arks=matched,
    )
    assert len(raw) == 1
    assert matched == {
        "ark:/12148/cb465971480", "ark:/12148/cb465971481",
    }
