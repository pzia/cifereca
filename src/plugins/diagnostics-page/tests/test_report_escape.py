"""Report Escape leaves unrelated keys and the collapsed state untouched."""
from types import SimpleNamespace

import pytest

from cifereca_diagnostics_page import _install_report_escape


class Controller:
    def set_propagation_phase(self, phase):
        self.phase = phase

    def connect(self, signal, callback):
        assert signal == "key-pressed"
        self.callback = callback


class Resize:
    def __init__(self, label):
        self.label = label

    def get_label(self):
        return self.label

    def set_label(self, label):
        self.label = label


def report(label="Réduire"):
    attached, sizes = [], []
    gtk = SimpleNamespace(EventControllerKey=Controller,
                          PropagationPhase=SimpleNamespace(CAPTURE="capture"))
    gdk = SimpleNamespace(KEY_Escape=65307, ModifierType=SimpleNamespace(
        CONTROL_MASK=4, ALT_MASK=8, SUPER_MASK=64, META_MASK=128))
    resize = Resize(label)
    _install_report_escape(
        gtk, gdk, SimpleNamespace(add_controller=attached.append),
        SimpleNamespace(set_size_request=lambda *size: sizes.append(size)), resize,
    )
    assert len(attached) == 1
    assert attached[0].phase == "capture"
    return attached[0], sizes, resize


def test_escape_reduces_expanded_report_once_without_closing_it():
    controller, sizes, resize = report()
    assert controller.callback(controller, 65307, 9, 0) is True
    assert sizes == [(-1, 320)]
    assert resize.label == "Agrandir"
    assert controller.callback(controller, 65307, 9, 0) is False
    assert sizes == [(-1, 320)]


@pytest.mark.parametrize("key,state", [
    (99, 4),  # Ctrl+C is still handled by TextView.
    (97, 4),  # Ctrl+A selection remains available.
    (65289, 0), (65289, 1),  # Tab / Shift+Tab navigation.
    (65293, 0), (32, 0),  # Enter / Space button activation.
    (65307, 4), (65307, 8), (65307, 64), (65307, 128),
])
def test_unrelated_and_modified_keys_propagate(key, state):
    controller, sizes, resize = report()
    assert controller.callback(controller, key, 0, state) is False
    assert sizes == []
    assert resize.label == "Réduire"


def test_escape_on_already_reduced_report_propagates():
    controller, sizes, resize = report("Agrandir")
    assert controller.callback(controller, 65307, 9, 0) is False
    assert sizes == []
    assert resize.label == "Agrandir"
