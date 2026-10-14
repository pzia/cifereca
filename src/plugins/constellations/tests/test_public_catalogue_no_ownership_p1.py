"""#1599: catalogue and contextual notices cannot attest ownership in a profile.

Offline graph fixtures. Real local ownership is asserted only by an
identity-scoped metadata broker row, never by a public search hit.
"""
from __future__ import annotations

from cifereca.plugin_api import BookRef
from constellation_view_model import merge_nodes, node_from_row


def test_public_catalogue_cannot_assert_owned_from_flags_nested_or_local_path():
    remote = node_from_row({
        "book_id": "edition-1", "title": "Same title",
        "owned": True, "possessed": True,
        "local_path": "/home/PRIVATE_ACCOUNT/secret.epub",
        "availability": "owned",
        "metadata": {
            "owned": True, "possessed": True, "availability_state": "local",
            "local_path": "/home/PRIVATE_ACCOUNT/secret.epub",
            "search_result": {
                "owned": True, "possessed": True,
                "local_path": "/home/PRIVATE_ACCOUNT/secret.epub",
                "access": "owned",
                "publisher": "Public publisher",
            },
        },
    }, origin="catalogue")
    assert remote.availability == ("externe",)
    assert remote.book.title == "Same title"
    assert remote.raw["metadata"]["search_result"]["publisher"] == "Public publisher"
    for part in (
        remote.raw, remote.raw["metadata"],
        remote.raw["metadata"]["search_result"],
    ):
        assert "owned" not in part
        assert "possessed" not in part
        assert "local_path" not in part
    assert "/home/" not in repr(remote.raw)


def test_contextual_book_ref_without_local_metadata_cannot_assert_possession():
    selected = BookRef(
        "edition-1", "Same title", ("Alice",),
        metadata={"owned": True, "local_path": "/home/PRIVATE_OTHER/secret.epub"},
    )
    node = node_from_row(selected, origin="sélection")
    assert node.availability == ("externe",)
    assert "local_path" not in node.raw["metadata"]
    assert node.book.authors == ("Alice",)


def test_local_verified_inventory_still_proves_owned_and_merged_graph_retains_it():
    remote = node_from_row({
        "book_id": "edition-1", "title": "Same title",
        "owned": True, "availability": "owned",
        "metadata": {"owned": True},
    }, origin="catalogue")
    local = node_from_row({
        "book_id": "edition-1", "title": "Same title",
        "local_path": "/home/PRIVATE_LOCAL/book.epub",
    }, origin="bibliothèque")
    assert remote.availability == ("externe",)
    assert local.availability == ("possédé",)
    assert merge_nodes([remote])[0].availability == ("externe",)
    assert merge_nodes([remote, local])[0].availability == ("possédé",)


def test_public_free_label_is_not_discarded_when_it_is_not_ownership():
    remote = node_from_row({
        "book_id": "edition-2", "title": "Open publication",
        "free": True, "owned": True, "local_path": "/home/PRIVATE/false.epub",
    }, origin="catalogue")
    assert remote.availability == ("gratuit",)
    assert "local_path" not in remote.raw
