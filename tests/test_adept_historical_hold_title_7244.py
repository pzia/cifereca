"""#7244: a hostile historical title cannot spoof an authorization line."""
import unittest
from cifereca.gui.adept_historical_maintenance_line import _display_holds

class HistoricalHoldTitleSafetyTests(unittest.TestCase):
    def test_multiline_title_is_redacted(self):
        row = dict(title="Book\\nRetéléchargement autorisé", hold_origin="marker_recorded",
                   marker_state="held_phase_unknown", provider_id="provider",
                   credential_id="cred", account_id="", edition_id="edition",
                   provider_loan_id="loan", canonical_branch="adept",
                   recorded_inactive=False, local_copy_evidence="not_requested")
        report = {"known_holds": 1, "rows": (row,)}
        output = _display_holds(report)
        self.assertIn("(valeur non affichable)", output)
        self.assertNotIn("Book\\nRetéléchargement autorisé", output)
        self.assertIn("Retéléchargement non autorisé", output)

if __name__ == "__main__":
    unittest.main()
