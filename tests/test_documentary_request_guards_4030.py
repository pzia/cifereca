"""#4030: invalid documentary searches never dispatch a catalogue query."""
import unittest
from unittest.mock import patch
from cifereca.core.local_origin_documentary_suggestions import suggest_known_public_documents

class DocumentaryRequestGuardsTests(unittest.TestCase):
    def test_invalid_input_never_queries_catalogue(self):
        invalid = (
            {"title": "x"},
            {"title": "Valid title", "page": True},
            {"title": "Valid title", "page": 201},
            {"title": "Valid title", "page_size": 0},
            {"title": "Valid title", "page_size": 51},
            {"title": "Valid\\ntitle"},
            {"title": "Valid title", "author": "A\\tB"},
        )
        with patch("cifereca.core.local_origin_documentary_suggestions.search_public_notice_c") as search:
            for params in invalid:
                params = {k: v.replace("\\\\n", "\\n").replace("\\\\t", "\\t") if isinstance(v, str) else v for k,v in params.items()}
                with self.subTest(params=params), self.assertRaisesRegex(ValueError, "invalid_documentary_suggestion_request"):
                    suggest_known_public_documents(None, **params)
            search.assert_not_called()

if __name__ == "__main__":
    unittest.main()
