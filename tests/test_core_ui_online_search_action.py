import ast
from pathlib import Path
import unittest


class OnlineSearchActionCopy(unittest.TestCase):
    def test_online_action_explains_that_it_extends_current_query(self):
        source = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
        tree = ast.parse(source.read_text())
        window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
        method = next(node for node in window.body if isinstance(node, ast.FunctionDef) and node.name == "_build_search_page")
        rendered = ast.unparse(method)

        self.assertIn("Gtk.Button(label='Étendre en ligne')", rendered)
        self.assertIn(
            "Interroger les catalogues distants avec la requête et les filtres actuels",
            rendered,
        )
        self.assertNotIn("Gtk.Button(label='En ligne')", rendered)


if __name__ == "__main__":
    unittest.main()
