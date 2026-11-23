"""#4030: unsafe OPF source URL claims never reach public C storage."""
import unittest
from unittest.mock import Mock
from cifereca.core.local_origin_documentary_suggestions import _matching_source_url

class DocumentaryOpfUrlGuardsTests(unittest.TestCase):
    def test_invalid_urls_do_not_access_storage(self):
        storage = Mock()
        for url in (None, 42, "", "x" * 4097, "https://a.invalid/\\nspoof",
                    "https://a.invalid/\\tspoof"):
            with self.subTest(url=repr(url)):
                self.assertIsNone(_matching_source_url(storage, (object(),), url))
        storage.assert_not_called()

if __name__ == "__main__":
    unittest.main()
