from tests.gui_source import gui_source_text
import ast
from pathlib import Path
import unittest


class DetailBadgeLayout(unittest.TestCase):
    def test_detail_badges_wrap_and_keep_full_tooltip(self):
        source = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
        tree = ast.parse(gui_source_text(source))
        detail = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DetailPanel")
        method = next(node for node in detail.body if isinstance(node, ast.FunctionDef) and node.name == "_cover_and_title")
        rendered = ast.unparse(method)

        self.assertIn("badges_box = Gtk.FlowBox()", rendered)
        self.assertIn("badges_box.set_min_children_per_line(1)", rendered)
        self.assertIn("badges_box.set_max_children_per_line(4)", rendered)
        self.assertIn("badges_box.set_halign(Gtk.Align.START)", rendered)
        self.assertIn("chip.set_wrap(True)", rendered)
        self.assertIn("chip.set_wrap_mode(Pango.WrapMode.WORD_CHAR)", rendered)
        self.assertIn("chip.set_tooltip_text(str(value))", rendered)
        self.assertNotIn("badges_box = Gtk.Box", rendered)


if __name__ == "__main__":
    unittest.main()
