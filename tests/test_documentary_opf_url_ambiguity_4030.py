"""#4030: no URL corroboration without a unique public hit."""
import unittest
from unittest.mock import Mock
from cifereca.core.local_origin_documentary_suggestions import _matching_source_url

class DocumentaryOpfUrlAmbiguityTests(unittest.TestCase):
    def test_empty_page_never_selects_origin(self):
        self.assertIsNone(_matching_source_url(Mock(), (), "https://example.invalid/book"))

    def test_invalid_claim_type_never_selects_origin(self):
        self.assertIsNone(_matching_source_url(Mock(), (object(),), ["https://example.invalid"]))

if __name__ == "__main__":
    unittest.main()
