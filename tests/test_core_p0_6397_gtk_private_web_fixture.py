"""Validate the exact private native E2E seed independently of GTK."""
from contextlib import closing
from pathlib import Path
from runpy import run_path

from cifereca.core.notice_c_authenticated_web_choices import resolve_authenticated_web_choice
from cifereca.core.notice_c_private_search_web import read_authenticated_web_for_search
from cifereca.core.search_orchestration import capture_search_read_scope
from cifereca.storage import Storage


def test_native_private_web_seed_reopens_with_exact_reader_and_eviction(tmp_path):
    module = Path(__file__).resolve().parents[1] / "e2e" / "notice_c_private_web_menu_contract.py"
    fixture = run_path(str(module))
    filename = tmp_path / "private-web.sqlite"
    with closing(Storage(filename)) as db:
        rows = fixture["seed_private_web_choices"](db)
    with closing(Storage(filename)) as db:
        core = fixture["OfflineCore"](db)
        scope = capture_search_read_scope(core, fixture["OWNER"])
        foreign = capture_search_read_scope(core, fixture["FOREIGN"])
        for row, expected in rows:
            menu = read_authenticated_web_for_search(core, row, reader_scope=scope)
            assert menu is not None
            actual = tuple(resolve_authenticated_web_choice(
                core, menu, choice_id=choice.choice_id) for choice in menu.choices)
            assert actual == expected
            assert read_authenticated_web_for_search(core, row, reader_scope=foreign) is None
        assert db.entitlements() == db.acquisitions() == db.placements() == []
