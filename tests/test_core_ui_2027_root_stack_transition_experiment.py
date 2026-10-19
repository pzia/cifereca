"""#2027 root Stack transition isolation: only top-level startup/main changes."""
from pathlib import Path


def test_root_stack_transition_is_disabled_without_disabling_child_navigation():
    root = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
    window = (root / "main_window.py").read_text()
    shell = (root / "shell_body_controller.py").read_text()
    revealer = (root / "notification_widgets.py").read_text()
    assert "self.root_stack.set_transition_type(Gtk.StackTransitionType.NONE)" in window
    assert "window.view_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)" in shell
    assert "revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)" in revealer
