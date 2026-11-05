"""P1 #942: only safe, actually rendered bibliographic rows count.

Run the real shared DetailPanel method with small fake GTK widgets. The test
makes no GTK display, HTTP request or clipboard access during rendering.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from tests.test_core_0940_detail_actions import _group


def _render_detail(pairs, kinds):
    render, clipboard = _group()
    groups = []
    actions = []
    window = NS(
        service=NS(current_identity=NS(identity_id="profile-a")),
        search_books=lambda *args, **kwargs: actions.append((args, kwargs)),
        toast=lambda _message: None,
    )
    host = NS(window=window, _append_content=groups.append)
    render(host, "Détail", pairs, fields=tuple(NS(kind=kind) for kind in kinds))
    return groups, actions, clipboard


def test_mixed_public_private_metadata_counts_only_visible_rows():
    groups, actions, clipboard = _render_detail(
        [("Auteur", "Alice"), ("Notice interne", "https://example.test/?token=secret"),
         ("Auteur", "Bob"), ("Authentification", "Bearer secret-value"),
         ("Sujet", "nature")],
        ["author", "all", "author", "all", "subject"],
    )
    assert len(groups) == 1
    expander = groups[0].children[0]
    assert expander.title == "Détail"
    assert expander.subtitle == "3 champ(s) bibliographiques"
    assert [(row.title, row.subtitle) for row in expander.children[0].children] == [
        ("Auteur", "Alice"), ("Auteur", "Bob"), ("Sujet", "nature"),
    ]
    assert actions == [] and clipboard.value is None
    # Each bibliographic value retains its own copy callback, even with
    # repeated labels (two different authors).
    bob_copy = expander.children[0].children[1].children[0]
    bob_copy.click()
    assert clipboard.value == "Bob"
    assert windowless_no_search(actions)


def windowless_no_search(actions):
    return actions == []


def test_one_visible_row_never_counts_rejected_links():
    groups, _, _ = _render_detail(
        [("Notice privée", "https://example.test/?token=secret"), ("Auteur", "Alice")],
        ["all", "author"],
    )
    assert len(groups) == 1
    expander = groups[0].children[0]
    assert expander.subtitle == "1 champ(s) bibliographiques"
    assert len(expander.children[0].children) == 1


def test_all_rejected_metadata_does_not_create_empty_expander():
    groups, actions, clipboard = _render_detail(
        [("Notice privée", "https://example.test/?token=secret"),
         ("Authorization", "Bearer secret-value")],
        ["all", "all"],
    )
    assert groups == [] and actions == [] and clipboard.value is None
