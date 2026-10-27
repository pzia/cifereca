"""#1023: visible search editions remain distinct after Core mirror-v2."""
from dataclasses import replace

from tests.test_search_results_view import _book, _host


def _known(key, edition, *, credential="credential", qualified=True):
    return {
        "key": key, "title": "Dumas", "author": "Author",
        "provider_id": "paris", "item_id": "same",
        "state": "remote_catalog", "local": False,
        "metadata": {
            "source_credential": credential,
            "docbase": edition,
            "source_collection": "cat-A",
            "catalogue_scope_version": 2 if qualified else None,
        },
    }


def _remote(edition, *, ident="same"):
    return replace(
        _book("paris", "same"), id=ident,
        docbase=edition, source_collection="cat-A",
    )


def test_remote_edition_a_does_not_hide_known_b_or_attach_ambiguous_placement():
    host = _host()
    a = _remote("EDITION-A")
    host._search_local_objects = {
        "known:a": _known("known:a", "EDITION-A"),
        "known:b": _known("known:b", "EDITION-B"),
    }
    host.service.local_placements_for_sources.return_value = {
        ("paris", "credential", "same"): {"placement_id": "from-unspecified-edition"},
    }
    host._remote_search_results = [a]
    host._populate_search([a], announce=False)
    rows = host.search_page.show_rows.call_args.args[0]
    assert [row.key for row in rows] == ["known:b", "paris:same"]
    assert rows[-1].options["action_label"] is None
    assert rows[-1].options["action_callback"] is None
    host._open_placement.assert_not_called()


def test_unqualified_historical_known_notice_is_not_silently_merged_with_live():
    host = _host()
    host._search_local_objects = {
        "known:legacy": _known("known:legacy", "EDITION-A", qualified=False),
    }
    books = [_remote("EDITION-A")]
    host._remote_search_results = books
    host._populate_search(books, announce=False)
    rows = host.search_page.show_rows.call_args.args[0]
    assert [row.key for row in rows] == ["known:legacy", "paris:same"]


def test_two_remote_editions_do_not_share_one_legacy_placement_callback():
    host = _host()
    host.service.local_placements_for_sources.return_value = {
        ("paris", "credential", "same"): {"placement_id": "one-legacy-link"},
    }
    books = [
        _remote("EDITION-A", ident="A"),
        _remote("EDITION-B", ident="B"),
    ]
    host._remote_search_results = books
    host._populate_search(books, announce=False)
    rows = host.search_page.show_rows.call_args.args[0]
    assert [r.key for r in rows] == ["paris:A", "paris:B"]
    assert all(r.options["action_label"] is None for r in rows)


def test_remote_results_with_shared_supplier_ui_id_remain_independently_selectable():
    """A Paris id omits credential/collection even when mirror-v2 distinguishes both."""
    host = _host()
    first = _remote("EDITION-A")
    second = replace(first, title="Collection B", source_collection="cat-B")
    third = replace(first, title="Compte B", source_credential="credential-B")
    books = [first, second, third]
    host._remote_search_results = books
    host._populate_search(books, announce=False)
    rows = host.search_page.show_rows.call_args.args[0]
    assert len(rows) == 3
    keys = [row.key for row in rows]
    assert len(set(keys)) == 3
    assert len(host.search_objects) == 3
    assert {host.search_objects[key].title for key in keys} == {
        "Dumas", "Collection B", "Compte B",
    }
    assert all(row.options["action_callback"] is None for row in rows)

    # A re-ordered progressive batch must not swap the selected supplier
    # edition under an existing UI key.
    original_keys = {item.title: key for key, item in host.search_objects.items()}
    host._remote_search_results = list(reversed(books))
    host._populate_search(host._remote_search_results, announce=False)
    assert {item.title: key for key, item in host.search_objects.items()} == original_keys


def test_unqualified_remote_id_collision_keeps_both_rows_without_false_scope():
    host = _host()
    first = replace(_remote("EDITION-A"), source_credential="", title="Historique A")
    second = replace(first, title="Historique B", docbase="")
    books = [first, second]
    host._remote_search_results = books
    host._populate_search(books, announce=False)
    rows = host.search_page.show_rows.call_args.args[0]
    assert len(rows) == 2
    assert len({row.key for row in rows}) == 2
    assert len(host.search_objects) == 2
    assert all(row.options["action_callback"] is None for row in rows)
