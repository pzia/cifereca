"""#4682: real shared Detail method renders profile-separated attested access rows.

Run the production _group branch with lightweight GTK shape doubles so the
same structure is checked by Core CI even without a display.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.local_book_detail_sections import _library_access_rows


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"


class Widget:
    def __init__(self, *args, **kwargs):
        self.children = []
        self.title = kwargs.get("label", "")
        self.subtitle = ""
        self.tooltip = ""
        self.classes = set()

    def add_css_class(self, value):
        self.classes.add(value)

    def set_title(self, text):
        self.title = text

    def set_subtitle(self, text):
        self.subtitle = text

    def set_tooltip_text(self, text):
        self.tooltip = text

    def set_use_markup(self, value):
        assert value is False

    def set_subtitle_lines(self, value):
        assert value == 1

    def append(self, child):
        self.children.append(child)

    def add(self, child):
        self.children.append(child)


def _real_group():
    root = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(n for n in root.body
               if isinstance(n, ast.ClassDef) and n.name == "DetailPanel")
    method = next(n for n in cls.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_group")
    env = {
        "Gtk": NS(Box=Widget, Label=Widget,
                  Orientation=NS(VERTICAL="vertical")),
        "Adw": NS(PreferencesGroup=Widget, ActionRow=Widget),
    }
    code = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    exec(compile(code, str(SOURCE), "exec"), env)
    return env["_group"]


def test_real_detail_renders_attested_per_profile_access_as_typed_rows():
    item = {
        "placements": [
            {"identity_id": "alice", "filename": "opaque.epub",
             "state": "present", "storage_target_id": "internal"},
            {"identity_id": "bob", "filename": "borrowed.epub", "state": "present"},
        ],
        "source_refs": [
            {"identity_id": "alice", "provider_id": "kobo"},
            {"identity_id": "bob", "provider_id": "bnf-pret"},
        ],
        "possessions": [
            {"identity_id": "alice", "provider_id": "kobo",
             "kind": "owned", "state": "active"},
        ],
        "media_format": "epub",
    }
    rows = _library_access_rows(
        item, provider_display_name=lambda provider: provider,
        identity_by_id=lambda identity: NS(label=identity.upper()),
        sync_label="",
    )
    host = NS(content=[], _append_content=lambda widget: host.content.append(widget))
    _real_group()(host, "Bibliothèques et accès", rows)
    assert len(host.content) == 1
    group = host.content[0]
    assert group.title == "Bibliothèques et accès"
    assert len(group.children) == 2
    alice, bob = group.children
    assert alice.children[0].title == "ALICE"
    assert bob.children[0].title == "BOB"
    assert [row.title for row in alice.children[1:]] == [
        "Fichier local", "Source", "Achat possédé en ligne",
    ]
    assert [row.title for row in bob.children[1:]] == [
        "Fichier local", "Source",
    ]
    assert "opaque.epub" in alice.children[1].subtitle
    assert "opaque.epub" in alice.children[1].tooltip
    assert all(row.tooltip for card in (alice, bob)
               for row in card.children[1:])
    assert not any("Achat" in row.title for row in bob.children[1:])


def test_detail_access_rows_do_not_infer_missing_rights_from_file_or_source():
    rows = [("Profil A", "\\n".join(("Fichier local : book.epub", "Source : Kobo")))]
    host = NS(content=[], _append_content=lambda widget: host.content.append(widget))
    _real_group()(host, "Bibliothèques et accès", rows)
    assert [row.title for row in host.content[0].children[0].children[1:]] == [
        "Fichier local", "Source",
    ]
