"""#2385: actual public provider observation -> durable C -> six read modes."""
import pytest

from cifereca.storage import Storage
from cifereca.core.notice_c_catalogue_query import search_public_catalogue_view
from tests.test_core_p0_6366_public_c_sqlite_source_search import (
    verified_public, persist,
)


@pytest.mark.parametrize("mode", (
    "list", "grid", "compact", "detail", "sources", "timeline",
))
def test_six_catalogue_modes_reopen_real_persisted_public_c(tmp_path, mode):
    path = tmp_path / "public-c.sqlite"
    storage = Storage(path)
    persist(storage, verified_public(
        record_id="public-edition", title="Les loups du Nord",
        description="Roman des forêts",
    ))
    storage.close()

    reopened = Storage(path)
    try:
        view = search_public_catalogue_view(
            reopened, "loups", mode=mode, now=100,
        )
        assert view.status == "partial"
        assert view.known_matches == 1
        assert len(view.rows) == 1
        row = view.rows[0]
        assert row.title == "Les loups du Nord"
        assert row.provider_id
        assert row.document.kind in ("public_catalogue", "catalogue")
        assert row.description == ("Roman des forêts" if mode == "detail" else None)
        assert reopened.entitlements() == []
        assert reopened.acquisitions() == []
    finally:
        reopened.close()
