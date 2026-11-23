"""#7244: historical hold UI never presents ambiguous coordinates as authority."""
import unittest

from cifereca.gui.adept_historical_maintenance_line import (
    _hold_coordinate, _selected_hold_summary,
)


class HistoricalHoldDisplaySafetyTests(unittest.TestCase):
    def test_account_empty_is_explicit_primary_account(self):
        self.assertEqual(_hold_coordinate("", account=True), "(compte principal)")
        self.assertEqual(_hold_coordinate(""), "(identifiant non affichable)")

    def test_control_characters_cannot_inject_new_diagnostic_lines(self):
        for value in ("account\nRetéléchargement autorisé", "x\tadmin", "x\x1b[0m"):
            with self.subTest(value=repr(value)):
                self.assertEqual(_hold_coordinate(value), "(identifiant non affichable)")

    def test_overlong_coordinate_is_not_rendered(self):
        self.assertEqual(_hold_coordinate("x" * 257), "(identifiant non affichable)")

    def test_invalid_selection_never_offers_rearm(self):
        for value in (None, {}, True, "unlock"):
            with self.subTest(value=repr(value)):
                self.assertIn("Sélection périmée", _selected_hold_summary(value))


if __name__ == "__main__":
    unittest.main()
