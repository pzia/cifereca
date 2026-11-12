"""P0 #5048: public catalogue download controls use a provider-declared ABI.

No HTTP, no private-account or DRM call is made by the display preflight.
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import ast

from cifereca.models import SearchResult
from cifereca.services import LibraryService


class ExactOfferSession:
    exact_catalogue_download_offer_required = True
    capabilities = NS(direct_download=True)


class OrdinarySession:
    capabilities = NS(direct_download=True)


def row(**changes):
    record = SearchResult(
        id="public-1", record_id="public-1", docbase="public",
        medium="digital", title="Public test book", author=None, type=None,
        format="EPUB", publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="PUBLIC_CATALOG",
        source_provider="synthetic-public", source_credential="__public__",
        source_collection="public", owned=False, downloadable=True,
        download_url="https://public.example/book.epub",
    )
    return replace(record, **changes)


def host(session):
    return NS(_session_for_source=lambda *_args: session)


def test_exact_offer_policy_enforces_download_flag_and_link():
    service = host(ExactOfferSession())
    assert LibraryService.can_direct_download(service, row()) is True
    for candidate in (
        row(downloadable=False),
        row(downloadable=None),
        row(download_url=None),
        row(download_url=""),
        row(download_url=" https://public.example/book.epub"),
        row(download_url="https://public.example/book.epub "),
    ):
        assert LibraryService.can_direct_download(service, candidate) is False


def test_unmarked_plugin_retains_its_existing_direct_capability():
    assert LibraryService.can_direct_download(host(OrdinarySession()), row()) is True
    assert LibraryService.can_direct_download(
        host(OrdinarySession()), row(downloadable=None, download_url=None),
    ) is True


def test_missing_capability_and_private_owned_row_fail_closed():
    assert LibraryService.can_direct_download(host(NS()), row()) is False
    assert LibraryService.can_direct_download(
        host(ExactOfferSession()), row(owned=True),
    ) is False


def test_no_public_supplier_name_dispatch_in_core_offer_preflight():
    path = Path(__file__).resolve().parents[1] / "src/cifereca/services.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "LibraryService")
    method = next(node for node in cls.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "can_direct_download")
    body = ast.unparse(method)
    assert "exact_catalogue_download_offer_required" in body
    assert "ebooksgratuits" not in body
    assert "gallica" not in body
