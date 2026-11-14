from types import SimpleNamespace as NS

from cifereca.catalogue_link import catalogue_url
from cifereca.services import LibraryService
from cifereca.providers.base import AccountOptionField, ProviderCapabilities, ProviderDescriptor


def test_friendly_metadata_is_preferred_to_delivery_url():
    row = {
        "metadata": {"search_result": {"friendly_url": "https://catalogue.test/book/1"}},
        "download_url": "https://delivery.test/loan/secret",
    }
    assert catalogue_url(row) == "https://catalogue.test/book/1"


def test_delivery_signed_and_unsafe_urls_are_rejected():
    urls = [
        "file:///book.epub",
        "javascript:alert(1)",
        "https://u:p@host/book",
        "https://host/loan/secret",
        "https://host/%256coan/secret",
        "https://host/book?TOKEN=secret",
        "https://host/book?X-Amz-Credential=secret",
        "https://host/book.acsm",
        "https://host:444/book",
    ]
    for url in urls:
        assert catalogue_url({"friendly_url": url}) is None


def test_relative_paris_notice_uses_configured_public_site():
    host = NS(
        provider_descriptors=lambda: [ProviderDescriptor(
            id="paris", display_name="Paris", description="public",
            state="ready", capabilities=ProviderCapabilities(),
            homepage="https://default.test",
            public_catalogue_base_option_key="base_url",
            account_options=(AccountOptionField(
                "base_url", "Portal", legacy_config_key="base_url",
            ),),
        )],
        config=NS(get=lambda key, default: (
            "https://paris.test" if key == "base_url" else default
        )),
    )

    assert LibraryService.public_catalogue_url(
        host,
        {
            "source_provider": "paris",
            "source_credential": "one",
            "friendly_url": "/notice/42",
        },
    ) == "https://paris.test/notice/42"


def test_missing_notice_uses_declared_public_homepage():
    host = NS(provider_descriptors=lambda: [NS(id="other", homepage="https://catalogue.test")])

    assert LibraryService.public_catalogue_url(host, {"provider_id": "other"}) == "https://catalogue.test"


def test_unknown_origin_uses_configured_public_catalogue_without_fabricated_notice():
    host = NS(
        provider_descriptors=lambda: [],
        config=NS(get=lambda key, default: "https://public.test"),
    )

    assert LibraryService.public_catalogue_url(
        host,
        {"provider_id": "local", "title": "Livre"},
    ) == "https://public.test"
