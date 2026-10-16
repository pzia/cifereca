"""P1 #1867: Accueil must not turn store ownership into loans or local files."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


MODULE = Path(__file__).resolve().parents[1] / "cifereca_home.py"
spec = importlib.util.spec_from_file_location("cifereca_home_1867", MODULE)
home = importlib.util.module_from_spec(spec)
spec.loader.exec_module(home)


class OverviewOwnershipTests(unittest.TestCase):
    def test_numilog_source_is_named_even_without_local_cached_books(self):
        row = {"providers": ["numilog"], "statistics": {"local_books": 0}}
        grouped = home.source_groups(row)
        assert grouped["authenticated"][0]["provider_id"] == "numilog"
        assert grouped["authenticated"][0]["label"] == "Numilog"
        assert grouped["local"] == []

    def test_store_only_has_zero_loans_but_owned_rights_not_local_files(self):
        stats = {"local_books": 0, "remote_owned_rights": 5, "known_loans": 0}
        rows = home.overview_counts_lines(stats)
        self.assertEqual(rows[0], "Livres locaux : 0 · prêts de bibliothèque connus : 0")
        self.assertIn("Droits d’achat distants : 5", rows[1])
        self.assertIn("ne prouvent pas la présence d’un fichier local", rows[1])
        self.assertNotIn("emprunts connus", " ".join(rows))
        self.assertEqual(stats["known_loans"], 0)

    def test_real_library_loans_survive_mixed_store_and_local_counts(self):
        rows = home.overview_counts_lines({
            "local_books": 2, "remote_owned_rights": 3, "known_loans": 1,
        })
        self.assertIn("Livres locaux : 2", rows[0])
        self.assertIn("prêts de bibliothèque connus : 1", rows[0])
        self.assertIn("Droits d’achat distants : 3", rows[1])

    def test_missing_counts_are_unknown_not_a_fake_zero(self):
        rows = home.overview_counts_lines({
            "local_books": 0, "known_loans": None,
            "remote_owned_rights": None,
        })
        self.assertIn("prêts de bibliothèque connus : —", rows[0])
        self.assertIn("Droits d’achat distants : —", rows[1])
        self.assertNotIn("connus : 0", rows[0])
        self.assertIn("Livres locaux : 0", rows[0])

    def test_malformed_counts_are_never_displayed_as_synthetic_entitlements(self):
        rows = home.overview_counts_lines({
            "local_books": True, "remote_owned_rights": -5,
            "known_loans": "https://private.example/?token=SECRET",
        })
        self.assertNotIn("SECRET", repr(rows))
        self.assertNotIn("-5", repr(rows))
        self.assertEqual(rows[0], "Livres locaux : — · prêts de bibliothèque connus : —")
        self.assertIn("Droits d’achat distants : —", rows[1])


if __name__ == "__main__":
    unittest.main()
