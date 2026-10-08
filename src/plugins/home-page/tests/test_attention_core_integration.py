from __future__ import annotations

from cifereca_home import attention_items


def test_attention_items_from_public_dashboard_projection():
    row = {
        "identity_id": "alexandre",
        "providers": ["paris"],
        "sources": [
            {
                "provider_id": "paris",
                "label": "Bibliothèques de Paris",
                "connected": False,
            }
        ],
        "adept_profile_id": None,
        "sync": {
            "status": "partial",
            "new_acquisitions": 0,
            "attention_required": 2,
            "errors": 1,
        },
        "statistics": {
            "identity_id": "alexandre",
            "local_books": 12,
            "local_placements": 12,
            "known_loans": 3,
            "loan_sources_observed": 1,
            "loans_observed_at": 100,
            "remote_owned_rights": 0,
            "formats": {"epub": 12},
            "sources": {"paris": 3},
            "epub_covers": {"extracted": 12},
            "actions": {},
            "scope": "persisted_inventory_last_observed_provider_loans_and_action_window",
        },
    }

    items = attention_items(row, now=100 + 25 * 3600)
    labels = [item["label"] for item in items]

    assert "Synchronisation à reprendre" in labels
    assert "Inventaire des prêts à actualiser" not in labels
    assert "Bibliothèques de Paris · connexion requise" in labels
    assert "Profil ADE à associer" in labels

    # The public projection does not expose these optional counters here.
    # Home must not fabricate them; support remains forward-compatible.
    assert "Prêts proches de l’échéance" not in labels
    assert "Réservations disponibles" not in labels
