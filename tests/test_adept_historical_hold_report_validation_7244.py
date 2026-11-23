"""#7244: malformed historical hold reports fail closed in the GUI."""
import unittest
from cifereca.gui.adept_historical_maintenance_line import _display_holds

class HistoricalHoldReportValidationTests(unittest.TestCase):
    def test_malformed_reports_are_unavailable(self):
        for report in (None, {}, {"rows": []}, {"rows": (), "known_holds": None},
                       {"rows": (), "known_holds": -1},
                       {"rows": (None,), "known_holds": 1},
                       {"rows": ({"title": "x"},), "known_holds": 1}):
            with self.subTest(report=repr(report)):
                self.assertIn("Verrous indisponibles", _display_holds(report))

if __name__ == "__main__":
    unittest.main()
