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
AGE_TAGS = {"UNIMARC": frozenset({"329", "333"}),
            "INTERMARC": frozenset({"833"})}
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


def exact_edition_age_fields(xml: bytes, isbn: str) -> list[dict[str, object]]:
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
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ValueError("sru_entities_forbidden")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError("invalid_sru_xml") from exc
    if root.tag != SRU + "searchRetrieveResponse":
        raise ValueError("invalid_sru_root")
    if root.find(SRU + "numberOfRecords") is None:
        raise ValueError("missing_sru_count")
    if root.find(".//" + SRU + "diagnostics") is not None:
        raise ValueError("sru_diagnostics")
    records = root.findall("./" + SRU + "records/" + SRU + "record")
    if len(records) > MAX_RECORDS:
        raise ValueError("sru_record_limit")
    output: list[dict[str, object]] = []
    for wrapper in records:
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
                    "age_status": "unqualified_raw_evidence",
                })
    return output
