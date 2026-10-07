from __future__ import annotations
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("cifereca_home",ROOT/"cifereca_home.py")
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class AttentionCoverageTests(unittest.TestCase):
    def test_attention_aggregates_known_actions(self):
        row={
            "providers":["paris"],
            "sources":[{"provider_id":"paris","label":"Paris","connected":False}],
            "statistics":{"known_loans":2,"loans_observed_at":100,"due_soon_loans":1,"available_bookings":2},
            "sync":{"attention_required":3,"errors":1},
        }
        labels=[item["label"] for item in mod.attention_items(row,now=100+25*3600)]
        self.assertIn("Prêts proches de l’échéance",labels)
        self.assertIn("Réservations disponibles",labels)
        self.assertIn("Inventaire des prêts ancien",labels)
        self.assertTrue(any(label.startswith("Sources à connecter") for label in labels))
        self.assertTrue(any(label.startswith("Profil ADE") for label in labels))

if __name__=="__main__":
    unittest.main()
