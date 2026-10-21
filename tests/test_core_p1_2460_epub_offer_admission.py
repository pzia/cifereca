"""#2460: an unqualified 'Distant' catalogue notice must not offer EPUB download.

A declared public provider capability != a book-specific acquisition offer.
Only the user can trigger the actual acquisition, which remains provider-guarded.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.services import LibraryService


EPUB = "https://opds2.ebooksgratuits.org/index.php/fetch/42.epub"


def item(*, provider="ebooksgratuits", downloadable=None, link=None, metadata=None):
    return NS(
        source_provider=provider,
        source_credential="public",
        download_url=link,
        downloadable=downloadable,
        metadata=metadata or {},
    )


class CatalogFixture:
    def __init__(self, *, direct=True):
        self.calls = []
        self.session = NS(capabilities=NS(direct_download=direct))
        self.acquisition = NS(
            override_command=lambda _provider: None,
            acquisition_url=lambda _item: None,
        )

    def _session_for_source(self, provider, credential):
        self.calls.append((provider, credential))
        return self.session


@pytest.mark.parametrize("downloadable,link", [
    (None, None),
    (False, EPUB),
    (None, EPUB),
    (True, None),
    (True, ""),
    (True, " " + EPUB),
    (True, EPUB + "\n"),
])
def test_unqualified_ebooksgratuits_remote_notice_cannot_offer_download(
    downloadable, link,
):
    service = CatalogFixture()
    result = LibraryService.can_direct_download(
        service, item(downloadable=downloadable, link=link),
    )
    assert result is False
    assert service.calls == [("ebooksgratuits", "public")]


def test_exact_fresh_opds_epub_offer_remains_explicitly_downloadable():
    service = CatalogFixture()
    offered = item(downloadable=True, link=EPUB)
    assert LibraryService.can_direct_download(service, offered) is True
    assert service.calls == [("ebooksgratuits", "public")]


def test_even_valid_epub_link_without_provider_capability_never_enables_action():
    service = CatalogFixture(direct=False)
    assert LibraryService.can_direct_download(
        service, item(downloadable=True, link=EPUB),
    ) is False


def test_stale_known_catalogue_never_triggers_session_or_download_button():
    service = CatalogFixture()
    stale = item(
        downloadable=True, link=EPUB,
        metadata={"search_provenance": "known_catalog_stale"},
    )
    assert LibraryService.can_direct_download(service, stale) is False
    assert service.calls == []


def test_other_provider_contract_is_unchanged():
    service = CatalogFixture()
    other = item(provider="gutenberg", downloadable=None, link=None)
    assert LibraryService.can_direct_download(service, other) is True


def test_no_http_or_implicit_acquisition_in_admission_check():
    service = CatalogFixture()
    service.session.capabilities = NS(direct_download=True)
    # can_direct_download only uses the local, already-loaded session's
    # manifest capability and the exact notice flags; it never probes GET.
    assert LibraryService.can_direct_download(
        service, item(downloadable=True, link=EPUB),
    )
    assert not hasattr(service, "transport")
