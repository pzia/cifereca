"""Verify the E2E multi-web seed without a GTK/display dependency."""
from contextlib import closing
from pathlib import Path
from runpy import run_path

from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.core.notice_c_web_choices import read_public_web_choices, resolve_public_web_choice
from cifereca.storage import Storage


def test_e2e_multiple_web_fixture_survives_exact_eviction_and_restart(tmp_path):
    module = Path(__file__).resolve().parents[1] / "e2e" / "notice_c_web_menu_contract.py"
    seed = run_path(str(module))["seed_multiple_web_choices"]
    filename = tmp_path / "multi-web.sqlite"
    with closing(Storage(filename)) as db:
        document, epoch, expected = seed(db)
    with closing(Storage(filename)) as db:
        menu = read_public_web_choices(db, document)
        assert menu is not None and menu.epoch == epoch
        assert len(menu.choices) == 3
        page = search_public_notice_c(
            db, "recette menu multiple", now=100, group_by_document=True,
        )
        assert page.known_documents == 1 and page.known_matches == 3
        assert len(page.hits) == 1 and page.hits[0].document == document
        assert {hit.provider_id for hit in page.matching_sources} == {
            "seller-a", "seller-b", "seller-c",
        }
        for viewer in ("alice", "bob", "carol"):
            rows = merge_public_notice_c_known_rows([], page, identity_id=viewer)
            assert len(rows) == 1 and rows[0]["_cover_document_ref"] == document
            assert not rows[0].get("download_url")
        assert {choice.label: resolve_public_web_choice(db, document, expected_epoch=epoch,
                    choice_id=choice.choice_id) for choice in menu.choices} == expected
        assert db.entitlements() == db.acquisitions() == db.placements() == []
