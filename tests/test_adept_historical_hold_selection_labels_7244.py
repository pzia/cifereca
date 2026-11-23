"""#7244: historic hold selection uses the same bounded display labels."""
import pathlib
import unittest

class HistoricalHoldSelectionLabelTests(unittest.TestCase):
    def test_selection_never_interpolates_untrusted_fields_directly(self):
        source = pathlib.Path("src/cifereca/gui/adept_historical_maintenance_line.py").read_text()
        self.assertIn("f\"{index}. {_ledger_label(item['title'])}", source)
        self.assertIn("_hold_coordinate(item['provider_loan_id'])", source)
        self.assertNotIn("f\"{index}. {item['title']}", source)

if __name__ == "__main__":
    unittest.main()
