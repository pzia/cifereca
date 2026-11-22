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


def test_choices_with_same_provider_record_across_editions_are_distinct(tmp_path, monkeypatch):
    from tests import test_core_p0_6397_c_web_reference_persistence as fixtures
    original = fixtures.verified_public
    with closing(Storage(tmp_path / "same-record.sqlite")) as db:
        monkeypatch.setattr(fixtures, "verified_public",
                            lambda **kw: original(revision_id="edition-a", **kw))
        a = source(db, "seller-a", "same", [link("seller-a", URL), link("seller-a", OTHER)])
        monkeypatch.setattr(fixtures, "verified_public",
                            lambda **kw: original(revision_id="edition-b", **kw))
        b = source(db, "seller-a", "same", [link("seller-a", URL)])
        assert a.document != b.document
        target = a.observation.key.edition_id
        publish(db, target, a)
        result = publish(db, target, b)
        menu = read_public_web_choices(db, result.document)
        assert len(menu.choices) == 3
        assert {opt.label for opt in menu.choices} == {
            "seller-a — same (1/3)", "seller-a — same (2/3)", "seller-a — same (3/3)",
        }
        assert len({opt.choice_id for opt in menu.choices}) == 3
        assert sorted(choose(db, menu, opt) for opt in menu.choices) == sorted([URL, URL, OTHER])
        assert read_public_web_choices(db, result.document) == menu
        for item in (a, b):
            assert evict_notice_cover(db, item.document, expected_epoch=item.epoch)
        assert read_public_web_choices(db, result.document) == menu


def test_corrupt_source_fence_only_disqualifies_its_own_web_choices(tmp_path):
    from cifereca.core.notice_cover_repository import _fence_key

    filename = tmp_path / "corrupt-source.sqlite"
    with closing(Storage(filename)) as db:
        a = source(db, "seller-a", "a", [link("seller-a", URL)])
        b = source(db, "seller-b", "b", [link("seller-b", OTHER)])
        target = a.observation.key.edition_id
        publish(db, target, a)
        result = publish(db, target, b)
        menu = read_public_web_choices(db, result.document)
        by_provider = {option.provider_id: option for option in menu.choices}
        assert choose(db, menu, by_provider["seller-a"]) == URL
        assert choose(db, menu, by_provider["seller-b"]) == OTHER
        with db._lock, db._connect() as sql:
            sql.execute("UPDATE metadata SET value=? WHERE key=?",
                        ("{}", _fence_key(_opaque_key(a.document))))
        reduced = read_public_web_choices(db, result.document)
        assert reduced.epoch == menu.epoch
        assert reduced.choices == (by_provider["seller-b"],)
        assert choose(db, menu, by_provider["seller-a"]) is None
        assert choose(db, menu, by_provider["seller-b"]) == OTHER
        assert evict_notice_cover(db, b.document, expected_epoch=b.epoch)
    with closing(Storage(filename)) as db:
        assert read_public_web_choices(db, result.document) == reduced
        assert choose(db, menu, by_provider["seller-a"]) is None
        assert choose(db, menu, by_provider["seller-b"]) == OTHER
        assert db.entitlements() == [] and db.acquisitions() == []


def test_corrupt_canonical_row_is_not_suppressed_as_missing_source(tmp_path):
    import pytest
    from cifereca.core.notice_c_cover_index import _db_key

    with closing(Storage(tmp_path / "corrupt-canonical.sqlite")) as db:
        a = source(db, "seller-a", "a", [link("seller-a", URL)])
        result = publish(db, a.observation.key.edition_id, a)
        menu = read_public_web_choices(db, result.document)
        with db._lock, db._connect() as sql:
            sql.execute("UPDATE metadata SET value=? WHERE key=?",
                        ("{}", _db_key(result.document)))
        with pytest.raises(ValueError):
            read_public_web_choices(db, result.document)
        with pytest.raises(ValueError):
            choose(db, menu, menu.choices[0])


def test_corrected_link_keeps_other_provider_and_same_provider_record_after_restart(tmp_path):
    """#6397: replacement of a single contribution, not of the whole menu."""
    filename = tmp_path / "corrected-choices.sqlite"
    unchanged = "https://books.example.org/title/independent"
    corrected = "https://books.example.org/title/corrected"
    with closing(Storage(filename)) as db:
        a = source(db, "seller-a", "a", [link("seller-a", URL)])
        b = source(db, "seller-a", "b", [link("seller-a", OTHER)])
        c = source(db, "seller-b", "c", [link("seller-b", unchanged)])
        target = a.observation.key.edition_id
        for item in (a, b, c):
            aggregate = publish(db, target, item)
        old = read_public_web_choices(db, aggregate.document)
        old_by_record = {option.record_id: option for option in old.choices}
        assert set(old_by_record) == {"a", "b", "c"}
        assert {record: choose(db, old, option)
                for record, option in old_by_record.items()} == {
                    "a": URL, "b": OTHER, "c": unchanged,
                }

        # A new observation immediately invalidates only its own link even
        # before consolidation catches up. An independent choice cannot be
        # substituted when the obsolete choice is activated.
        updated = source(db, "seller-a", "a", [link("seller-a", corrected)], now=101)
        assert choose(db, old, old_by_record["a"]) is None
        assert choose(db, old, old_by_record["b"]) == OTHER
        assert choose(db, old, old_by_record["c"]) == unchanged
        pending = read_public_web_choices(db, aggregate.document)
        assert pending.epoch == old.epoch
        assert {option.record_id for option in pending.choices} == {"b", "c"}

        new_c = publish(db, target, updated)
        new = read_public_web_choices(db, new_c.document)
        assert new.document == old.document and new.epoch > old.epoch
        new_by_record = {option.record_id: option for option in new.choices}
        assert set(new_by_record) == set(old_by_record)
        assert all(choose(db, old, option) is None for option in old.choices)
        for item in (updated, b, c):
            assert evict_notice_cover(db, item.document, expected_epoch=item.epoch)
    with closing(Storage(filename)) as db:
        assert read_public_web_choices(db, new.document) == new
        assert {record: choose(db, new, option)
                for record, option in new_by_record.items()} == {
                    "a": corrected, "b": OTHER, "c": unchanged,
                }
        assert all(choose(db, old, option) is None for option in old.choices)
        assert db.entitlements() == db.acquisitions() == db.placements() == []
