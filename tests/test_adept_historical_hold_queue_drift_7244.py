"""#7244: a hold disappearing during a slow review invalidates its page."""
import unittest
from unittest.mock import patch
from cifereca.core.adept_historical_maintenance import (
    HistoricalReviewRequest, run_historical_hold_review,
)

class HistoricalHoldQueueDriftTests(unittest.TestCase):
    def test_queue_change_without_sql_stamp_change_revokes_page(self):
        service = object()
        request = object.__new__(HistoricalReviewRequest)
        object.__setattr__(request, "service", service)
        object.__setattr__(request, "identity", "identity")
        object.__setattr__(request, "database", "/tmp/not-used.sqlite")
        with patch.object(HistoricalReviewRequest, "bound", return_value=True), \\
             patch("cifereca.core.adept_historical_maintenance.sqlite_source_stamp",
                   return_value=("stable",)), \\
             patch("cifereca.core.loan_download_retry_review.review_loan_download_retries",
                   side_effect=[(), (object(),)]) as queue:
            self.assertIsNone(run_historical_hold_review(service, request))
            self.assertEqual(queue.call_count, 2)

if __name__ == "__main__":
    unittest.main()
