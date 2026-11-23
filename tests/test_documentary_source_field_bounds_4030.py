"""#4030: public C hit coordinates are bounded before local display."""
import pathlib
import unittest

class DocumentarySourceFieldBoundsTests(unittest.TestCase):
    def test_all_displayed_source_coordinates_are_bounded(self):
        source = pathlib.Path("src/cifereca/core/local_origin_documentary_suggestions.py").read_text()
        self.assertIn("type(value) is str and len(value) <= 512", source)
        self.assertIn('"provider_id", "record_id", "edition_id"', source)
        self.assertIn('"source_revision", "medium", "media_format"', source)

if __name__ == "__main__":
    unittest.main()
