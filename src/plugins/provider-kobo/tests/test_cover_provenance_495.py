from types import SimpleNamespace

from cifereca_kobo_catalog import KoboCatalogService


def test_catalog_cover_publishes_product_provenance_but_library_cover_does_not():
    catalog = KoboCatalogService(
        SimpleNamespace(base_url="https://vault.example/base"),
        credential_id="vault",
    )

    public = catalog.normalize_item({
        "product_id": "book-1",
        "title": "Book",
        "cover_url": "/covers/book.jpg",
        "collection": "catalog",
        "owned": False,
    })
    assert public.thumbnail_url == "https://vault.example/covers/book.jpg"
    assert public.metadata["catalog_cover"] == {
        "url": "https://vault.example/covers/book.jpg",
        "source": "kobo.catalog",
        "evidence_id": "kobo:book-1",
        "evidence_strength": 100,
    }

    personal = catalog.normalize_item({
        "product_id": "book-1",
        "title": "Book",
        "cover_url": "/covers/book.jpg",
        "collection": "library",
        "owned": True,
    })
    assert personal.thumbnail_url == "https://vault.example/covers/book.jpg"
    assert "catalog_cover" not in personal.metadata
