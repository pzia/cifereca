"""P0 #1129: real facet binding and GTK boundary, without voluntary ADEPT return."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.detail_facets_controller import add_facets


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "detail_facets_controller.py"


class Widget:
    def __init__(self):
        self.children = []
        self.visible = True

    def get_first_child(self):
        return self.children[0] if self.children else None

    def get_next_sibling(self):
        return None

    def set_visible(self, value):
        self.visible = value


class Row(Widget):
    def __init__(self, title, subtitle):
        super().__init__()
        self.title, self.subtitle = title, subtitle
        self.suffix = []
        self.activatable = True
        self.css_classes = set()

    def has_css_class(self, css_class):
        return css_class in self.css_classes

    def get_title(self):
        return self.title

    def get_subtitle(self):
        return self.subtitle

    def set_subtitle(self, value):
        self.subtitle = value

    def set_activatable(self, value):
        self.activatable = value

    def add_suffix(self, value):
        self.suffix.append(value)

    def remove(self, value):
        self.suffix.remove(value)


class Expander(Row):
    pass


class FacetGroup(Widget):
    def __init__(self, values, field):
        super().__init__()
        self.values, self.field = list(values), field

    def set_tooltip_text(self, value):
        self.tooltip = value

    def add_css_class(self, value):
        self.css_class = value

    def get_parent(self):
        return None


class Button:
    def __init__(self, label):
        self.label = label
        self.handlers = {}
        self.enabled = True

    def set_tooltip_text(self, value):
        self.tooltip = value

    def connect(self, event, handler):
        self.handlers[event] = handler

    def set_sensitive(self, enabled):
        self.enabled = enabled

    def click(self):
        self.handlers["clicked"](self)


def _execute(window, detail, item=None):
    return add_facets(
        window, detail, item or {}, object(),
        Adw=NS(ActionRow=Row, ExpanderRow=Expander),
        Gtk=NS(Button=Button),
        TaskPriority=NS(USER_ACTION=1),
    )


def _host(values=(), *, adept=None):
    groups = []
    service = NS(
        current_identity=NS(identity_id="alice"),
        adept_return_for_artifact=lambda *args: adept,
    )
    host = NS(
        _facet_values=lambda _item: values,
        _facet_value_group=lambda choices, field, _controls: (
            groups.append((field, tuple(choices))) or FacetGroup(choices, field)
        ),
        service=service,
        runner=NS(submit=lambda *a, **kw: None),
    )
    return host, groups


def test_essential_metadata_with_typed_actions_keeps_its_attested_value():
    row = Row("Auteur", "Alice Exemple")
    row.css_classes.add("detail-metadata-actions")
    host, groups = _host([("author", "Autre auteur", "autre")])
    _execute(host, NS(content=row, _subtitle_label=None))
    assert row.subtitle == "Alice Exemple"
    assert groups == []


def test_extracted_facets_are_headless_and_mainwindow_facade_is_stable():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(n) for n in ast.walk(tree)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("main_window" in i or i == "import gi" or i.startswith("from gi ")
                   for i in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    window = next(n for n in main.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    facade = next(n for n in window.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_add_facets")
    assert [a.arg for a in facade.args.args] == ["self", "detail", "item", "controls"]
    source = ast.unparse(facade)
    assert "detail_facets_controller.add_facets" in source
    assert "Gtk=Gtk" in source and "Adw=Adw" in source


def test_rebinding_replaces_only_inline_values_and_never_enters_detail_expander():
    outer = Row("Auteur", "Auteur EPUB")
    previous = FacetGroup([("Vieux", "old")], "author")
    outer.suffix.append(previous)
    outer._cifereca_facet_values = previous
    inside = Row("Auteur", "Original complet")
    expander = Expander("Détail", "Plusieurs champs")
    expander.children = [inside]
    parent = Widget()
    parent.children = [outer, expander]
    # The real GTK sibling iterator traverses next children: test independently
    # with a container where first_child represents the first encountered row.
    detail = NS(content=outer, _subtitle_label=None, add_actions=lambda *_: None)
    host, groups = _host([("author", "Auteur de catalogue", "Auteur-ID")])
    _execute(host, detail)
    assert outer._cifereca_facet_original_subtitle == "Auteur EPUB"
    assert outer.subtitle == ""
    assert previous not in outer.suffix
    assert outer._cifereca_facet_values.values == [("Auteur de catalogue", "Auteur-ID")]
    assert groups == [("author", (("Auteur de catalogue", "Auteur-ID"),))]
    _execute(host, detail)
    assert len(outer.suffix) == 1
    assert outer._cifereca_facet_original_subtitle == "Auteur EPUB"

    detail.content = expander
    _execute(host, detail)
    assert inside.subtitle == "Original complet"
    assert not hasattr(inside, "_cifereca_facet_values")


def test_unprojected_publisher_uses_preserved_original_subtitle_after_refresh():
    row = Row("Éditeur", "Éditeur EPUB")
    detail = NS(content=row, _subtitle_label=None, add_actions=lambda *_: None)
    host, groups = _host([])
    _execute(host, detail)
    assert groups == [("publisher", (("Éditeur EPUB", "Éditeur EPUB"),))]
    assert row.subtitle == ""
    assert row.activatable is False
    _execute(host, detail)
    assert len(row.suffix) == 1


def test_adept_return_cannot_be_invoked_from_any_detail_facet():
    """#4048: no facet API, button, task or ADEPT return when an origin exists."""
    binding = {"acquisition_id": "acq-7"}
    host, groups = _host([], adept=binding)
    events, requested, added = [], [], []
    host.service.adept_return_for_artifact = lambda *args: (
        events.append(("legacy-lookup", args)) or binding
    )
    host.service.return_adept_acquisition = lambda *_a: (
        events.append(("FORBIDDEN RETURN",)) or {}
    )
    host.runner.submit = lambda *args, **kw: requested.append((args, kw))
    detail = NS(content=Widget(), _subtitle_label=None,
                add_actions=lambda actions: added.extend(actions))
    _execute(host, detail, {"artifact_id": "artifact-7"})
    assert added == []
    assert requested == []
    assert events == []
    # Rebinding is read-only, including after an identity context switch.
    host.service.current_identity = NS(identity_id="other")
    _execute(host, detail, {"artifact_id": "artifact-7"})
    assert added == requested == events == []


def test_facets_module_has_no_return_dispatch_or_adept_lookup():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    names = {getattr(node, "attr", "") for node in ast.walk(tree)}
    assert "return_adept_acquisition" not in names
    assert "adept_return_for_artifact" not in names
    assert "submit" not in names
    assert not any(isinstance(node, ast.Constant)
                   and node.value == "Restituer le prêt ADEPT"
                   for node in ast.walk(tree))
