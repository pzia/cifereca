"""Offline BnF SRU MARC age *evidence* probe; never a canonical age (#4530).

Caller supplies the public SRU XML obtained under an independently authorized,
bounded request. This module performs no HTTP, persistence or entitlement logic.
Only exact own-ISBN matches are returned; contextual editions are excluded.
"""
from __future__ import annotations

import re
from xml.etree import ElementTree as ET

SRU = "{http://www.loc.gov/zing/srw/}"
MXC = "{info:lc/xmlns/marcxchange-v2}"
MAX_XML_BYTES = 2_000_000
MAX_RECORDS = 50
MAX_FIELDS = 128
# 329 is a *critical review*, not a standalone audience age field.
# 333 is the audience note. Intermarc 833 mixes critical notes and CNLJ
# audience metadata; raw evidence alone cannot discriminate subfield roles.
AGE_TAGS = {"UNIMARC": frozenset({"329", "333"}),
            "INTERMARC": frozenset({"833"})}
FIELD_ROLES = {("UNIMARC", "329"): "cnlj_critical_review_not_age",
               ("UNIMARC", "333"): "audience_note_unqualified",
               ("INTERMARC", "833"): "cnlj_mixed_review_or_audience"}
ISBN_TAG = {"UNIMARC": "010", "INTERMARC": "020"}


def canonical_isbn(raw: object) -> str | None:
    """Validated ISBN-13 comparison key; never changes the source field."""
    if type(raw) is not str or len(raw) > 64:
        return None
    clean = re.sub(r"[\s-]", "", raw).upper()
    if re.fullmatch(r"[0-9]{9}[0-9X]", clean):
        if sum((10 - i) * (10 if c == "X" else int(c))
               for i, c in enumerate(clean)) % 11:
            return None
        digits = "978" + clean[:9]
        check = (10 - sum(int(d) * (1 if i % 2 == 0 else 3)
                          for i, d in enumerate(digits)) % 10) % 10
        return digits + str(check)
    if re.fullmatch(r"[0-9]{13}", clean):
        if sum(int(d) * (1 if i % 2 == 0 else 3)
               for i, d in enumerate(clean)) % 10 == 0:
            return clean
    return None


def exact_edition_age_fields(
    xml: bytes, isbn: str, *, _matched_arks: set[str] | None = None,
) -> list[dict[str, object]]:
    """Inspect BnF MarcXchange fields without guessing an age interval.

    Results are raw, bounded subfield pairs with exact bibliographic ARK and
    source format. Callers must not treat these as verified numeric ages.
    Unknown/malformed formats and ambiguous own-ISBN records fail closed.
    """
    key = canonical_isbn(isbn)
    if key is None:
        raise ValueError("invalid_isbn")
    if type(xml) is not bytes or not 0 < len(xml) <= MAX_XML_BYTES:
        raise ValueError("invalid_sru_size")
    # BnF SRU is UTF-8. Decode before checking for declarations so an XML
    # parser cannot auto-detect UTF-16 and expand an entity the guard missed.
    try:
        text = xml.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("invalid_sru_encoding") from None
    if "\x00" in text:
        raise ValueError("invalid_sru_encoding")
    markup = text.upper()
    if "<!DOCTYPE" in markup or "<!ENTITY" in markup:
        raise ValueError("sru_entities_forbidden")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError("invalid_sru_xml") from exc
    if root.tag != SRU + "searchRetrieveResponse":
        raise ValueError("invalid_sru_root")
    counts = root.findall(SRU + "numberOfRecords")
    if (len(counts) != 1 or counts[0].text is None
            or re.fullmatch(r"[0-9]{1,12}", counts[0].text.strip()) is None):
        raise ValueError("invalid_sru_count")
    declared_total = int(counts[0].text.strip())
    if root.find(".//" + SRU + "diagnostics") is not None:
        raise ValueError("sru_diagnostics")
    records = root.findall("./" + SRU + "records/" + SRU + "record")
    if len(records) > MAX_RECORDS:
        raise ValueError("sru_record_limit")
    if records and declared_total == 0:
        raise ValueError("sru_count_conflict")
    output: list[dict[str, object]] = []
    for wrapper in records:
        # BnF SRU specifies MARC records via recordSchema=marcxchange and
        # recordPacking=xml. Refuse missing/foreign wrappers, not just a
        # plausible-looking nested mxc:record from another schema.
        schemas = wrapper.findall(SRU + "recordSchema")
        packings = wrapper.findall(SRU + "recordPacking")
        if (len(schemas) != 1 or (schemas[0].text or "").strip().lower() != "marcxchange"
                or len(packings) != 1 or (packings[0].text or "").strip() != "xml"):
            continue
        data = wrapper.find(SRU + "recordData")
        if data is None or len(data) != 1:
            continue
        record = data[0]
        fmt = record.attrib.get("format", "").upper()
        ark = record.attrib.get("id")
        if (record.tag != MXC + "record" or fmt not in ISBN_TAG
                or record.attrib.get("type") != "Bibliographic"
                or not isinstance(ark, str)
                or re.fullmatch(r"ark:/12148/cb[0-9]+[a-z0-9]", ark) is None):
            continue
        fields = record.findall(MXC + "datafield")
        if len(fields) > MAX_FIELDS:
            continue
        own_isbns = []
        for field in fields:
            if field.get("tag") == ISBN_TAG[fmt]:
                for sub in field.findall(MXC + "subfield"):
                    if sub.get("code") == "a":
                        own_isbns.append(canonical_isbn(sub.text))
        if not own_isbns or any(value is None for value in own_isbns):
            continue
        # A notice with two distinct edition ISBNs cannot prove which one
        # the age metadata describes. Exact same-ISBN aliases are harmless.
        if set(own_isbns) != {key}:
            continue
        # Exact ISBN records without age fields still create ambiguity.
        # This optional internal collector preserves the public raw API.
        if _matched_arks is not None:
            _matched_arks.add(ark)
        for field in fields:
            tag = field.get("tag")
            if tag not in AGE_TAGS[fmt]:
                continue
            pairs = []
            for sub in field.findall(MXC + "subfield"):
                code, value = sub.get("code"), sub.text
                if (type(code) is str and re.fullmatch(r"[a-z0-9]", code)
                        and type(value) is str and 0 < len(value) <= 300):
                    pairs.append((code, value))
            if pairs:
                output.append({
                    "record_ark": ark, "own_isbn13": key, "marc_format": fmt,
                    "field_tag": tag, "raw_subfields": tuple(pairs),
                    "evidence_role": FIELD_ROLES[(fmt, tag)],
                    "age_status": "unqualified_raw_evidence",
                })
    return output


# Conservative qualification of *observed* CNLJ public SRU fields only.
# These are audience statements for the bibliographic record, not a claim
# about a purchased digital variant or a Core YouthAgeRange (#4530/#4552).
# Each JAg code here has been checked against the BnF reference and the
# corresponding public SRU fixture. Do not extrapolate a numeric grammar.
_OBSERVED_JAG = {
    "JAg1399": (13, None),
    "JAg0003": (0, 3),
    "JAg0699": (6, None),
}
_OBSERVED_CNLJ_NOTES = {
    "À partir de 13 ans": (13, None),
    "De 0 à 3 ans": (0, 3),
}


def qualified_observed_age_fields(xml: bytes, isbn: str) -> list[dict[str, object]]:
    """Qualify a single unambiguous own-ISBN BnF record, offline.

    No numeric value escapes if two distinct ARKs claim the same ISBN, or
    any audience field of the matched record is unknown or contradictory.
    Historical 329 critical reviews are not audience fields and do not veto
    a verified 333. No digital support or Core age range is inferred.
    """
    matched_arks: set[str] = set()
    observed = exact_edition_age_fields(xml, isbn, _matched_arks=matched_arks)
    audience = [
        row for row in observed
        if (row["marc_format"], row["field_tag"]) in {
            ("UNIMARC", "333"), ("INTERMARC", "833"),
        }
    ]
    if not audience:
        return []
    # Multiple ARKs with the same own ISBN are not proof of the one
    # manifestation meant by the caller; never pick the first SRU hit.
    if len(matched_arks) != 1:
        return []

    candidates: list[dict[str, object]] = []
    for row in audience:
        marker = (row["marc_format"], row["field_tag"])
        pairs = row["raw_subfields"]
        code = None
        bounds = None
        if marker == ("UNIMARC", "333"):
            # The *whole* field shape must be the attested 333$a + $2=CNLJ,
            # not just one recognizable subfield inside an unknown record.
            if len(pairs) == 2 and {key for key, _ in pairs} == {"a", "2"}:
                notes = [value for key, value in pairs if key == "a"]
                authority = [value for key, value in pairs if key == "2"]
                if len(notes) == 1 and authority == ["CNLJ"]:
                    code = notes[0]
                    bounds = _OBSERVED_CNLJ_NOTES.get(code)
        else:
            # An 833 with any additional/unknown subfield cannot be mapped
            # safely merely because one $m happens to resemble a JAg code.
            if len(pairs) == 1 and pairs[0][0] == "m":
                code = pairs[0][1]
                bounds = _OBSERVED_JAG.get(code)
        if bounds is None:
            # Reject the *entire* candidate record if any age-bearing
            # field is unqualified: no cherry-picking a favorable range.
            return []
        candidates.append({
            "record_ark": row["record_ark"],
            "own_isbn13": row["own_isbn13"],
            "marc_format": row["marc_format"],
            "field_tag": row["field_tag"],
            "source_subfield": "a" if marker[0] == "UNIMARC" else "m",
            "source_literal": code,
            "audience_age_min": bounds[0],
            "audience_age_max": bounds[1],
            "evidence_role": "cnlj_bibliographic_target_audience",
            "edition_support_verified": False,
            "core_youth_age_range_authorized": False,
        })
    if len({(row["audience_age_min"], row["audience_age_max"])
            for row in candidates}) != 1:
        return []
    # Duplicate identical 833$m fields are one attested observation.
    return candidates[:1]
