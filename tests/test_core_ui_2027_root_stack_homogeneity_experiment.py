"""Isolated #2027 root stack request policy, distinct from shell #2420."""
from pathlib import Path


def test_only_root_stack_vertical_homogeneity_changes():
    root = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
    main = (root / "main_window.py").read_text()
    shell = (root / "shell_body_controller.py").read_text()
    assert "self.root_stack.set_vhomogeneous(False)" in main
    assert "self.root_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)" in main
    assert "window.view_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)" in shell
    assert "window.busy_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)" in shell
