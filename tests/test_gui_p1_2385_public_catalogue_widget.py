"""#2385: six-mode GTK rendering uses only admitted public C facts."""
import pytest

from cifereca.core.notice_c_catalogue_views import project_public_catalogue_view
from cifereca.gui.notice_c_public_catalogue_widget import render_public_catalogue_view
from tests.test_core_p1_2385_catalogue_views import _page


class FakeWidget:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.children = []

    def append(self, child):
        self.children.append(child)

    def set_xalign(self, value):
        pass

    def set_wrap(self, value):
        pass


class FakeGtk:
    Box = FakeWidget
    Label = FakeWidget

    class Orientation:
        VERTICAL = 1


def _labels(widget):
    result = []
    if "label" in widget.kwargs:
        result.append(widget.kwargs["label"])
    for child in widget.children:
        result.extend(_labels(child))
    return result


@pytest.mark.parametrize("mode", (
    "list", "grid", "compact", "detail", "sources", "timeline",
))
def test_gtk_catalogue_renders_six_views_without_rights(mode):
    view = project_public_catalogue_view(_page(), mode=mode)
    labels = _labels(render_public_catalogue_view(view, Gtk=FakeGtk))
    assert any("Catalogue public" in label for label in labels)
    assert any("Book" in label for label in labels)
    assert any("Résultats partiels" in label for label in labels)
    assert not any("Emprunter" in label or "Télécharger" in label for label in labels)
    if mode == "detail":
        assert "Description" in labels
    if mode == "sources":
        assert sum(label == "Book" for label in labels) == 2


def test_gtk_catalogue_rejects_unverified_view():
    with pytest.raises(ValueError):
        render_public_catalogue_view(object(), Gtk=FakeGtk)
