"""#4030: invalid cache time cannot reach public catalogue query."""
import unittest
from unittest.mock import patch
from cifereca.core.local_origin_documentary_suggestions import suggest_known_public_documents

class DocumentaryNowGuardTests(unittest.TestCase):
    def test_invalid_now_is_rejected_before_catalogue_dispatch(self):
        with patch("cifereca.core.local_origin_documentary_suggestions.search_public_notice_c") as search:
            for now in (True, -1, "123", 1.5):
                with self.subTest(now=now), self.assertRaisesRegex(
                        ValueError, "invalid_documentary_suggestion_request"):
                    suggest_known_public_documents(None, title="Valid title", now=now)
            search.assert_not_called()

if __name__ == "__main__":
    unittest.main()
