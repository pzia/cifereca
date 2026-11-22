"""#7407: presentation grouping is declarative, not execution."""
from pathlib import Path
import json
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.maintenance_sections import GROUPS, presentation_group, build_sections, show_unavailable, finish_sections

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("caps,expected", [
    ((), "unclassified"),
    (("maintenance.group.integrity",), "integrity"),
    (("maintenance.group.integrity", "maintenance.group.quality"), "unclassified"),
    (("maintenance.group.unknown",), "unclassified"),
    (("other",), "unclassified"),
])
def test_group_selection(caps, expected):
    assert presentation_group(caps) == expected


def test_bundled_manifest_groups_exhaustive():
    manifests = [ROOT / "src/cifereca/builtin_ui/cifereca-plugin.json",
                 *ROOT.glob("src/plugins/*/cifereca-plugin.json")]
    rows = [p for path in manifests for p in json.loads(path.read_text())["plugins"]
            if p["type"] == "maintenance.tool"]
    assert rows
    assert all(presentation_group(p.get("capabilities", ())) != "unclassified" for p in rows)


class Widget:
    def __init__(self, **kwargs):
        self.children = []
        self.kwargs = kwargs
        self.visible = True
    def append(self, child):
        self.children.append(child)
    def add_css_class(self, _):
        pass
    def get_first_child(self):
        return self.children[0] if self.children else None
    def get_last_child(self):
        return self.children[-1] if self.children else None
    def set_visible(self, value):
        self.visible = value


Gtk = NS(Box=Widget, Label=Widget, Orientation=NS(VERTICAL=1))


def test_six_sections_empty_and_unavailable_without_service_calls():
    box = Widget()
    sections = build_sections(box, Gtk=Gtk)
    assert tuple(sections) == tuple(x[0] for x in GROUPS) + ("unclassified",)
    d = NS(type="maintenance.tool", id="test.held", display_name="Audit",
           capabilities=("maintenance.group.integrity",))
    show_unavailable(sections, [NS(descriptor=d, reason="private path")], (), Gtk=Gtk)
    finish_sections(sections, Gtk=Gtk)
    assert len(sections["integrity"].children) == 2
    assert "private path" not in sections["integrity"].children[-1].kwargs["label"]
    assert not sections["unclassified"].visible
    assert all(len(sections[key].children) == 2 for key, _ in GROUPS)


def test_active_registry_contribution_not_duplicated():
    sections = build_sections(Widget(), Gtk=Gtk)
    d = NS(type="maintenance.tool", id="test.active", display_name="Audit",
           capabilities=("maintenance.group.integrity",))
    show_unavailable(sections, [NS(descriptor=d)], [NS(plugin_id=d.id)], Gtk=Gtk)
    assert len(sections["integrity"].children) == 1
