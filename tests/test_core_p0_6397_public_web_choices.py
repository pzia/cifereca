"""#6397: exact public choice is not a cached browser URL or a new GET."""
from contextlib import closing

from cifereca.storage import Storage
from cifereca.core.notice_cover_repository import evict_notice_cover, _opaque_key
from cifereca.core.notice_c_web_choices import (
    read_public_web_choices, resolve_public_web_choice,
)
from tests.test_core_p0_6397_c_web_reference_persistence import source, link, publish


URL = "https://books.example.org/title/one"
OTHER = "https://books.example.org/title/two"


def choose(storage, menu, option):
    return resolve_public_web_choice(storage, menu.document,
                                     expected_epoch=menu.epoch, choice_id=option.choice_id)


def test_exact_choice_survives_qualified_eviction_and_restart(tmp_path):
    filename = tmp_path / "choice.sqlite"
    with closing(Storage(filename)) as db:
        a = source(db, "seller-a", "a", [link("seller-a", URL), link("seller-a", OTHER)])
        b = source(db, "seller-b", "b", [link("seller-b", URL)])
        c = source(db, "seller-c", "c", None)
        target = a.observation.key.edition_id
        for item in (a, b, c):
            result = publish(db, target, item)
        menu = read_public_web_choices(db, result.document)
        assert len(menu.choices) == 3
        assert len({opt.choice_id for opt in menu.choices}) == 3
        assert len({opt.label for opt in menu.choices}) == 3
        assert all(not hasattr(opt, "url") for opt in menu.choices)
        assert sorted(choose(db, menu, opt) for opt in menu.choices) == sorted([URL, URL, OTHER])
        for item in (a, b, c):
            assert evict_notice_cover(db, item.document, expected_epoch=item.epoch)
    with closing(Storage(filename)) as db:
        assert read_public_web_choices(db, result.document) == menu
        assert sorted(choose(db, menu, opt) for opt in menu.choices) == sorted([URL, URL, OTHER])
        assert db.entitlements() == [] and db.acquisitions() == []


def test_new_source_vetoes_old_choice_even_before_c_recompute(tmp_path):
    with closing(Storage(tmp_path / "changed.sqlite")) as db:
        a = source(db, "seller-a", "a", [link("seller-a", URL)])
        target = a.observation.key.edition_id
        c = publish(db, target, a)
        menu = read_public_web_choices(db, c.document)
        old, = menu.choices
        assert choose(db, menu, old) == URL
        newer = source(db, "seller-a", "a", [link("seller-a", OTHER)], now=101)
        assert read_public_web_choices(db, c.document).choices == ()
        assert choose(db, menu, old) is None
        # Even if a newer source is evicted before recompute, its fence
        # must not resurrect the superseded retained public web reference.
        assert evict_notice_cover(db, newer.document, expected_epoch=newer.epoch)
        assert choose(db, menu, old) is None
        newest = source(db, "seller-a", "a", [link("seller-a", OTHER)], now=102)
        updated = publish(db, target, newest)
        new_menu = read_public_web_choices(db, updated.document)
        assert choose(db, menu, old) is None
        assert choose(db, new_menu, new_menu.choices[0]) == OTHER
        assert resolve_public_web_choice(db, updated.document,
            expected_epoch=new_menu.epoch, choice_id=URL) is None


def test_foreign_c_same_url_and_unqualified_delete_are_not_click_proofs(tmp_path):
    with closing(Storage(tmp_path / "foreign.sqlite")) as db:
        a = source(db, "seller-a", "a", [link("seller-a", URL)])
        b = source(db, "seller-a", "b", [link("seller-a", URL)])
        ca = publish(db, a.observation.key.edition_id, a)
        cb = publish(db, b.observation.key.edition_id, b)
        ma = read_public_web_choices(db, ca.document)
        mb = read_public_web_choices(db, cb.document)
        assert choose(db, ma, mb.choices[0]) is None
        assert choose(db, mb, ma.choices[0]) is None
        with db._lock, db._connect() as sql:
            sql.execute("DELETE FROM metadata WHERE key=?", (_opaque_key(a.document),))
        assert choose(db, ma, ma.choices[0]) is None
        assert choose(db, mb, mb.choices[0]) == URL
        assert db.entitlements() == [] and db.acquisitions() == []
