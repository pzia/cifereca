"""#6397: one malformed C source does not suppress independent public web links."""
from contextlib import closing

import pytest

from cifereca.storage import Storage
from cifereca.core import notice_c_web_choices as web
from tests.test_core_p0_6397_c_web_reference_persistence import source, link, publish


@pytest.mark.parametrize("error", [ValueError, TypeError, KeyError, AttributeError])
def test_bad_source_only_revokes_own_web_choice(tmp_path, monkeypatch, error):
    with closing(Storage(tmp_path / "independent-web.sqlite")) as db:
        a = source(db, "seller-a", "a", [link("seller-a", "https://books.example.org/book/a")])
        b = source(db, "seller-b", "b", [link("seller-b", "https://books.example.org/book/b")])
        publish(db, a.observation.key.edition_id, a)
        canonical = publish(db, a.observation.key.edition_id, b).document
        all_choices = web.read_public_web_choices(db, canonical)
        assert {choice.provider_id for choice in all_choices.choices} == {"seller-a", "seller-b"}
        original = web._current_refs

        def guarded(sql, member):
            if member.provider_id == "seller-a":
                raise error("damaged source")
            return original(sql, member)

        monkeypatch.setattr(web, "_current_refs", guarded)
        visible = web.read_public_web_choices(db, canonical)
        assert visible.epoch == all_choices.epoch
        survivor, = visible.choices
        assert survivor.provider_id == "seller-b"
        old_by_provider = {choice.provider_id: choice for choice in all_choices.choices}
        assert web.resolve_public_web_choice(db, canonical,
            expected_epoch=all_choices.epoch,
            choice_id=old_by_provider["seller-a"].choice_id) is None
        assert web.resolve_public_web_choice(db, canonical,
            expected_epoch=visible.epoch,
            choice_id=survivor.choice_id) == "https://books.example.org/book/b"
        assert db.entitlements() == [] and db.acquisitions() == []
