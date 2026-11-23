"""#7244: selecting a historical hold cannot escape its bounded page."""
import unittest
from cifereca.core.adept_historical_maintenance import select_historical_hold

class HistoricalHoldOrdinalBoundTests(unittest.TestCase):
    def test_out_of_page_ordinals_rejected(self):
        for ordinal in (-1, 4, 2048, True, 1.0, "0"):
            with self.subTest(ordinal=ordinal):
                self.assertIsNone(select_historical_hold(None, None, {}, ordinal=ordinal, confirmed=True))

if __name__ == "__main__":
    unittest.main()
