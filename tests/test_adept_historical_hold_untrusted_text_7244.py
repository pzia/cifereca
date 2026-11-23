"""#7244: untrusted historical loan coordinates cannot forge diagnostic lines."""
import unittest
from cifereca.gui.adept_historical_maintenance_line import _hold_coordinate, _ledger_label

class HistoricalHoldUntrustedTextTests(unittest.TestCase):
    def test_control_characters_are_not_rendered(self):
        for value in ("foo\\nbar", "foo\\tbar", "foo\\rbar", "foo\\x1bbar"):
            decoded = value.encode().decode("unicode_escape")
            self.assertEqual(_hold_coordinate(decoded), "(identifiant non affichable)")
            self.assertEqual(_ledger_label(decoded), "(valeur non affichable)")

    def test_numeric_ledger_value_is_displayable(self):
        self.assertEqual(_ledger_label(0), "0")

if __name__ == "__main__":
    unittest.main()
