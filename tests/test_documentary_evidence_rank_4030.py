"""#4030: title/author comparisons are descriptive, never proof of origin."""
import unittest
from types import SimpleNamespace
from cifereca.core.local_origin_documentary_suggestions import (
    DocumentarySuggestion, _comparison,
)

class DocumentaryEvidenceRankTests(unittest.TestCase):
    def test_normalized_title_and_author_match(self):
        hit = SimpleNamespace(title="École des livres", author="Alice")
        self.assertEqual(_comparison(hit, "École des livres", "ALICE"),
                         "title_author_concordant")

    def test_author_mismatch_is_not_full_concordance(self):
        hit = SimpleNamespace(title="Same Title", author="Author One")
        self.assertEqual(_comparison(hit, "Same Title", "Author Two"),
                         "title_concordant_author_divergent")

    def test_missing_author_is_explicitly_unknown(self):
        hit = SimpleNamespace(title="Same Title", author=None)
        self.assertEqual(_comparison(hit, "Same Title", "Author"),
                         "title_concordant_author_unknown")

    def test_match_does_not_attest_provider_or_right(self):
        hit = SimpleNamespace(title="Same Title", author="Author")
        candidate = DocumentarySuggestion(hit, _comparison(hit, "Same Title", "Author"))
        self.assertFalse(candidate.association_authorized)
        self.assertFalse(candidate.provider_origin_attested)

if __name__ == "__main__":
    unittest.main()
