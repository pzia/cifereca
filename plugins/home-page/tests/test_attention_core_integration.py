from __future__ import annotations

from types import SimpleNamespace

from cifereca.services import LibraryService
from cifereca_home import attention_items


class Storage:
    def latest_sync_run(self, identity_id):
        assert identity_id == "alexandre"
        return {
            "status": "partial",
            "new_acquisitions": 0,
            "attention_required": 2,
            "errors": 1,
        }

    def adept_profile_binding_get(self, scope, identity_id):
        assert (scope, identity_id) == ("identity", "alexandre")
        return None

    def metadata_get(self, key):
        if key == "catalog_deep_last_at":
            return "1700000000"
        return None


class CoreDashboardFixture:
    """Minimal state consumed by the real LibraryService.dashboard_state()."""

    def __init__(self):
        self.storage = Storage()
        self.identities = [
            SimpleNamespace(
                identity_id="alexandre",
                label="Alexandre",
                directory_name="alexandre",
            )
        ]
        self.current_identity = self.identities[0]
        self.account = SimpleNamespace(
            identity_id="alexandre",
            provider_id="paris",
            credential_id="paris-main",
        )
        descriptor = SimpleNamespace(
            instantiation="multiple",
            display_name="Bibliothèques de Paris",
        )
        self._sessions = {
            "paris": SimpleNamespace(
                descriptor=descriptor,
                provider_id="paris",
                credential_id="paris-main",
                connected=False,
                capabilities=SimpleNamespace(
                    search=True,
                    loans=True,
                    history=True,
                    bookings=True,
                    borrow=True,
                    digital_download=True,
                    direct_download=False,
                    personal_library=False,
                    return_loan=True,
                    renew_loan=True,
                ),
            )
        }

    def accounts_for_identity(self, identity_id):
        return [self.account] if identity_id == "alexandre" else []

    def _provider_enabled_for_identity(self, provider_id, identity_id):
        return provider_id == "paris" and identity_id == "alexandre"

    def identity_statistics(self, identity_id):
        assert identity_id == "alexandre"
        # This shape mirrors the current Core044 public projection. In particular
        # it intentionally does not invent due_soon_loans/available_bookings.
        return {
            "identity_id": identity_id,
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
        }


def test_attention_items_from_real_core_dashboard_projection():
    core = CoreDashboardFixture()
    rows = LibraryService.dashboard_state(core)
    assert len(rows) == 1
    row = rows[0]

    items = attention_items(row, now=100 + 25 * 3600)
    labels = [item["label"] for item in items]

    assert "Actions de synchronisation requises" in labels
    assert "Erreurs de synchronisation" in labels
    assert "Inventaire des prêts ancien" in labels
    assert any(label.startswith("Sources à connecter : Bibliothèques de Paris") for label in labels)
    assert "Profil ADE à associer pour les prêts protégés" in labels

    # Core044 does not expose these counters in dashboard_state. Home must not
    # fabricate them; support remains forward-compatible if Core adds them later.
    assert "Prêts proches de l’échéance" not in labels
    assert "Réservations disponibles" not in labels
