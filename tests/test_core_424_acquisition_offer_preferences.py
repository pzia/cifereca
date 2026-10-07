from __future__ import annotations

from cifereca.core.search import available_formats, filter_loaded_results
from cifereca.plugin_api import (
    AcquisitionOffer,
    AcquisitionOfferSet,
    AcquisitionPreferences,
    ProviderCapabilities,
    ProviderDescriptor,
    ProviderSession,
    acquisition_format_projection,
    attested_offer_formats,
    normalize_acquisition_offers,
    rank_acquisition_offers,
)


def offer(offer_id: str, fmt: str = "", *, priority: int = 0, **metadata):
    return AcquisitionOffer(
        offer_id=offer_id,
        format=fmt,
        priority=priority,
        metadata=metadata,
        source={"url": f"https://example.test/{offer_id}.epub"},
    )


def test_legacy_offer_list_normalizes_without_breaking_provider_contract():
    result = normalize_acquisition_offers([
        offer("pdf", "application/pdf"),
        offer("epub", "application/epub+zip"),
    ])

    assert isinstance(result, AcquisitionOfferSet)
    assert [row.offer_id for row in result.offers] == ["pdf", "epub"]
    assert result.preferences == AcquisitionPreferences()
    assert attested_offer_formats(result) == ("pdf", "epub")


def test_only_explicit_offer_format_is_attested():
    result = AcquisitionOfferSet(offers=(
        offer("unknown", "", format_code="MONE", available_formats=["epub"]),
        AcquisitionOffer(
            offer_id="mono",
            format="MONO",
            source={"url": "https://example.test/book.pdf"},
        ),
    ))

    assert attested_offer_formats(result) == ()
    projection = acquisition_format_projection(result)
    assert projection["available_formats"] == ()
    assert projection["format_evidence"] == "unknown"


def test_projection_drives_existing_filters_from_attested_offers_only():
    choices = AcquisitionOfferSet(offers=(
        offer("epub", "epub"),
        offer("pdf", "pdf"),
    ))
    row = {
        "title": "Livre",
        "content_type": "ebook",
        "metadata": acquisition_format_projection(choices),
    }

    assert available_formats(row) == ("epub", "pdf")
    assert len(filter_loaded_results([row], content_types=["epub"])) == 1
    assert len(filter_loaded_results([row], content_types=["pdf"])) == 1
    assert filter_loaded_results([row], content_types=["mp3"]) == []


def test_explicit_format_preference_ranks_before_provider_priority():
    choices = AcquisitionOfferSet(
        offers=(
            offer("pdf-high", "pdf", priority=100),
            offer("epub-low", "epub", priority=1),
        ),
        preferences=AcquisitionPreferences(format_order=("epub",)),
    )

    ranked = rank_acquisition_offers(choices)

    assert [row.offer_id for row in ranked] == ["epub-low", "pdf-high"]


def test_explicit_offer_preference_is_stronger_than_format_preference():
    choices = AcquisitionOfferSet(
        offers=(
            offer("pdf-explicit", "pdf"),
            offer("epub", "epub", priority=100),
        ),
        preferences=AcquisitionPreferences(
            format_order=("epub",),
            offer_order=("pdf-explicit",),
        ),
    )

    assert [row.offer_id for row in rank_acquisition_offers(choices)] == [
        "pdf-explicit",
        "epub",
    ]


def test_epub_preference_accepts_attested_kepub_family_but_keeps_exact_format():
    choices = AcquisitionOfferSet(
        offers=(
            offer("pdf", "pdf", priority=100),
            offer("kepub", "kepub"),
        ),
        preferences=AcquisitionPreferences(format_order=("epub",)),
    )

    normalized = normalize_acquisition_offers(choices)

    assert attested_offer_formats(normalized) == ("pdf", "kepub")
    assert attested_offer_formats(normalized, families=True) == ("pdf", "epub")
    assert rank_acquisition_offers(normalized)[0].offer_id == "kepub"


def test_duplicate_offer_ids_are_rejected():
    choices = AcquisitionOfferSet(offers=(
        offer("same", "epub"),
        offer("same", "pdf"),
    ))

    try:
        normalize_acquisition_offers(choices)
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate offer ids must be rejected")


def test_incoherent_preferences_are_rejected():
    unavailable_format = AcquisitionOfferSet(
        offers=(offer("pdf", "pdf"),),
        preferences=AcquisitionPreferences(format_order=("epub",)),
    )
    unknown_offer = AcquisitionOfferSet(
        offers=(offer("pdf", "pdf"),),
        preferences=AcquisitionPreferences(offer_order=("missing",)),
    )

    for choices, expected in (
        (unavailable_format, "not attested"),
        (unknown_offer, "unknown acquisition offer"),
    ):
        try:
            normalize_acquisition_offers(choices)
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError("incoherent preference must be rejected")


class MinimalProvider(ProviderSession):
    def connect(self):
        self.connected = True
        return []

    def close(self):
        self.connected = False


def test_provider_base_keeps_legacy_empty_offer_contract():
    provider = MinimalProvider(
        descriptor=ProviderDescriptor(
            id="minimal",
            display_name="Minimal",
            description="Minimal provider",
            state="ready",
            capabilities=ProviderCapabilities(search=True),
        ),
        credential_id="public",
        label="Minimal",
        credentials={},
        options={},
    )

    assert provider.acquisition_offers(object()) == []


def test_multiformat_offer_set_preserves_alternatives_preference_and_core_projection():
    from cifereca.core.search import project_acquisition_offer_formats
    from cifereca.plugin_api import acquisition_offers_metadata
    multi = AcquisitionOffer(offer_id='multi', formats=('EPUB', 'pdf', 'MONE'), preferred_format='pdf')
    choices = AcquisitionOfferSet(offers=(multi,), preferences=AcquisitionPreferences(format_order=('pdf',)))
    assert attested_offer_formats(choices) == ('epub', 'pdf')
    assert acquisition_format_projection(choices)['preferred_formats'] == ('pdf',)
    assert acquisition_offers_metadata(choices)['preferred_format'] == 'pdf'
    projected = project_acquisition_offer_formats({'format': '', 'metadata': {}}, choices)
    assert projected['metadata']['available_formats'] == ['epub', 'pdf']
    assert projected['format'] == ''


def test_multiformat_ranking_uses_every_attested_alternative():
    multi = AcquisitionOffer(offer_id='multi', formats=('epub', 'pdf'))
    single = offer('epub', 'epub', priority=100)
    choices = AcquisitionOfferSet(offers=(single, multi), preferences=AcquisitionPreferences(format_order=('pdf',)))
    assert rank_acquisition_offers(choices) == (multi, single)


def test_offer_preferences_are_preserved_without_inventing_a_cross_offer_choice():
    from cifereca.plugin_api import acquisition_offers_metadata
    pdf = AcquisitionOffer(offer_id='pdf', formats=('epub', 'pdf'), preferred_format='pdf')
    epub = AcquisitionOffer(offer_id='epub', format='epub', preferred_format='epub')
    assert acquisition_format_projection([pdf])['preferred_formats'] == ('pdf',)
    assert acquisition_format_projection([pdf, epub])['preferred_formats'] == ()
    assert 'preferred_format' not in acquisition_offers_metadata(AcquisitionOfferSet(offers=(pdf, epub)))


def test_provider_has_exactly_one_acquisition_offers_method():
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).parents[1] / 'src/cifereca/providers/base.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ProviderSession')
    assert sum(isinstance(n, ast.FunctionDef) and n.name == 'acquisition_offers' for n in cls.body) == 1
