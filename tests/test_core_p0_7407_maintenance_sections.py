"""#7407: presentation grouping is declarative, not execution."""
from pathlib import Path
import json
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.maintenance_sections import GROUPS, presentation_group, build_sections, show_unavailable, finish_sections, add_section_links

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


def test_closed_status_vocabulary_and_no_config_or_error_leak():
    box = Widget()
    sections = build_sections(box, Gtk=Gtk)
    d = NS(type="maintenance.tool", id="audit.old", display_name="Audit historique",
           capabilities=("maintenance.group.organization",))
    show_unavailable(sections, [
        NS(descriptor=d, reason_code="maintenance_safety_hold", enabled=False,
           error="PRIVATE", reason_detail={"path": "SECRET"}),
        NS(descriptor=d, reason_code="missing_broker", enabled=True),
        NS(descriptor=d, reason_code="PRIVATE_UNKNOWN", enabled=False),
    ], (), Gtk=Gtk)
    labels = [w.kwargs["label"] for w in sections["organization"].children[1:]]
    assert labels == [
        "Audit historique — suspendu — garde de sécurité",
        "Audit historique — indisponible — service requis absent",
        "Audit historique — désactivé",
    ]


def test_declared_navigation_deduplicated_and_revocable():
    class NavigationWidget(Widget):
        root = object()
        def set_focusable(self, value): self.focusable = value
        def set_tooltip_text(self, value): self.tooltip = value
        def connect(self, signal, callback): self.callback = callback
        def get_root(self): return self.root
        def grab_focus(self): self.focus_count = getattr(self, "focus_count", 0) + 1

    gtk = NS(Box=NavigationWidget, Label=NavigationWidget, Button=NavigationWidget,
             Orientation=NS(VERTICAL=1))
    sections = build_sections(NavigationWidget(), Gtk=gtk)
    row = NS(capabilities=("maintenance.group.organization", "maintenance.link.recovery",
                           "maintenance.link.organization", "maintenance.link.fake"))
    live = [True]
    add_section_links(sections, [row, row], Gtk=gtk, current=lambda: live[0])
    heading = sections["recovery"].children[0]
    assert heading.focusable
    button, = sections["organization"].children[1:]
    assert button.kwargs["label"] == "Voir : Reprise des opérations"
    button.callback(button)
    assert heading.focus_count == 1
    heading.root = object()
    button.callback(button)
    assert heading.focus_count == 1
    heading.root = button.root
    live[0] = False
    button.callback(button)
    assert heading.focus_count == 1


def test_documented_inventory_is_exactly_the_installed_manifest_contract():
    """Do not receive #7407 with an omitted, duplicated or wrongly mapped tool."""
    manifests = [ROOT / "src/cifereca/builtin_ui/cifereca-plugin.json",
                 *ROOT.glob("src/plugins/*/cifereca-plugin.json")]
    descriptors = [p for path in manifests for p in json.loads(path.read_text())["plugins"]
                   if p["type"] == "maintenance.tool"]
    group_labels = {
        "organization": "Organisation", "integrity": "Intégrité",
        "quality": "Qualité", "provenance": "Provenance",
        "recovery": "Reprise", "storage": "Stockage",
    }
    document = (ROOT / "docs/CORE-P0-7407-MAINTENANCE-SECTIONS.md").read_text()
    rows = []
    for line in document.splitlines():
        if not line.startswith("| "):
            continue
        cells = tuple(part.strip() for part in line.strip("|").split("|"))
        if cells[0].startswith(("maintenance.", "core.maintenance.")):
            assert len(cells) == 4
            rows.append(cells)
    by_id = {r[0]: r for r in rows}
    assert len(by_id) == len(rows) == len(descriptors)
    assert set(by_id) == {p["id"] for p in descriptors}
    for descriptor in descriptors:
        _, group, contract, availability = by_id[descriptor["id"]]
        assert group == group_labels[presentation_group(descriptor.get("capabilities", ()))]
        ports, tickets = contract.split(" / ", 1)
        assert set(ports.split(", ")) == set(descriptor.get("requires_brokers", ()))
        assert tickets.startswith("#") and availability and availability != "Terminé"
    # Native adapters are not plugin manifests and cannot be counted as a
    # second installed maintenance command.
    for title in ("Adaptateur ADEPT historique — provenances",
                  "Adaptateur ADEPT historique — verrous",
                  "Téléchargements à reprendre (adaptateur CoreUI)"):
        assert document.count("| " + title + " |") == 1
