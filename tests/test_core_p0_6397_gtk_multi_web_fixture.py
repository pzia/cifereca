"""Verify the E2E multi-web seed without a GTK/display dependency."""
from contextlib import closing
from pathlib import Path
from runpy import run_path

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
        assert len(menu.choices) == 2
        assert {choice.label: resolve_public_web_choice(db, document, expected_epoch=epoch,
                    choice_id=choice.choice_id) for choice in menu.choices} == expected
        assert db.entitlements() == [] and db.acquisitions() == []
