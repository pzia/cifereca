"""#7244: historical title hints cannot hide leading/trailing whitespace."""
import unittest
from cifereca.core.adept_historical_maintenance import _hold_display_title

class HistoricalHoldTitleWhitespaceTests(unittest.TestCase):
    def test_bounded_title_is_preserved(self):
        self.assertEqual(_hold_display_title("Book title"), "Book title")

    def test_whitespace_wrapped_title_uses_neutral_fallback(self):
        for title in (" Book title", "Book title ", "  ", "\\nBook title"):
            with self.subTest(title=repr(title)):
                self.assertEqual(_hold_display_title(title), "Référence de prêt historique")

if __name__ == "__main__":
    unittest.main()
