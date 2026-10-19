from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "cifereca_bnf_catalogue", ROOT / "cifereca_bnf_catalogue.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def _records(*records: str) -> bytes:
    body = "".join(
        f"<srw:record><srw:recordData>{record}</srw:recordData></srw:record>"
        for record in records
    )
    return (
        '<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<srw:numberOfRecords>{len(records)}</srw:numberOfRecords>"
        f"<srw:records>{body}</srw:records>"
        "</srw:searchRetrieveResponse>"
    ).encode()


def _dc(*, ark: str, title: str = "Titre", formats=(), types=(), relations=(), identifiers=()):
    parts = [
        f"<dc:identifier>https://catalogue.bnf.fr/{ark}</dc:identifier>",
        f"<dc:title>{title}</dc:title>",
    ]
    parts.extend(f"<dc:format>{value}</dc:format>" for value in formats)
    parts.extend(f"<dc:type>{value}</dc:type>" for value in types)
    parts.extend(f"<dc:relation>{value}</dc:relation>" for value in relations)
    parts.extend(f"<dc:identifier>{value}</dc:identifier>" for value in identifiers)
    return "<dc:dc>" + "".join(parts) + "</dc:dc>"


def _parse(*records: str):
    # Parser-focused test double: keep the public ProviderSession identity
    # fields that real construction always initializes.
    session = object.__new__(mod.BnfCatalogueSession)
    session.provider_id = mod.DESCRIPTOR.id
    session.credential_id = "public"
    return session.parse_records(_records(*records))


def test_support_projection_distinguishes_physical_digital_and_unknown():
    rows = _parse(
        _dc(ark="ark:/12148/cb100000001a", title="Physique", formats=("1 vol. (120 p.) ; 20 cm",)),
        _dc(ark="ark:/12148/cb100000002b", title="Numérique", formats=("application/pdf",)),
        _dc(
            ark="ark:/12148/cb100000003c",
            title="Indéterminé",
            relations=("https://gallica.bnf.fr/ark:/12148/bpt6k123",),
        ),
    )
    by_title = {row.title: row for row in rows}

    assert by_title["Physique"].medium == "physical"
    assert by_title["Physique"].metadata["support"] == "physical"
    assert by_title["Numérique"].medium == "digital"
    assert by_title["Numérique"].metadata["support"] == "digital"
    assert by_title["Indéterminé"].medium == "unknown"
    assert by_title["Indéterminé"].metadata["support"] == "unknown"


def test_gallica_links_are_documentary_only_and_deduplicated():
    gallica = "https://gallica.bnf.fr/ark:/12148/bpt6k123"
    row = _parse(
        _dc(
            ark="ark:/12148/cb100000004d",
            relations=(gallica, gallica),
            identifiers=(gallica,),
        )
    )[0]

    assert row.friendly_url == "https://catalogue.bnf.fr/ark:/12148/cb100000004d"
    assert row.download_url is None
    assert row.downloadable is False
    assert row.medium == "unknown"
    assert row.metadata["document_links"] == [
        {"relation": "gallica", "url": gallica},
    ]
    assert "gallica_links" not in row.metadata
    assert "medium_fallback" not in row.metadata


def test_non_gallica_urls_are_not_published_as_document_links():
    row = _parse(
        _dc(
            ark="ark:/12148/cb100000005e",
            relations=("https://example.test/document",),
            identifiers=("https://example.test/other",),
        )
    )[0]

    assert row.metadata["document_links"] == []
