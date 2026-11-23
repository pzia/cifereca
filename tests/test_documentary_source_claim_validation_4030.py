"""#4030: malformed OPF source claims fail before any C lookup."""
import unittest
from unittest.mock import patch
from cifereca.core.local_origin_documentary_suggestions import suggest_known_public_documents

class DocumentarySourceClaimValidationTests(unittest.TestCase):
    def test_unsafe_claims_never_query_c(self):
        cases = (None, [], {"url"}, frozenset({"a", "b"}),
                 frozenset({123}), frozenset({""}),
                 frozenset({"x" * 4097}), frozenset({"https://x/\\nspoof"}))
        with patch("cifereca.core.local_origin_documentary_suggestions.search_public_notice_c") as search:
            for claim in cases:
                with self.subTest(claim=repr(claim)), self.assertRaisesRegex(
                        ValueError, "invalid_documentary_suggestion_request"):
                    suggest_known_public_documents(None, title="Valid title", source_urls=claim)
            search.assert_not_called()

if __name__ == "__main__":
    unittest.main()
