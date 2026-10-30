"""#3197/#3732: Core read-only library audit remains private after retiring Maintenance reconciliation UI."""
from __future__ import annotations

import json
from types import SimpleNamespace

from cifereca.core.library_audit_broker import LibraryReconciliationReadBrokerImpl
from tests.test_library_audit_broker import scoped


def test_real_broker_emits_closed_progress_without_private_identifiers(scoped, tmp_path):
    service, _, _ = scoped
    events = []
    control = SimpleNamespace(cancelled=lambda: False, report=lambda **kw: events.append(kw))
    report = LibraryReconciliationReadBrokerImpl(service).audit(
        identity_id="alice", control=control,
    )
    assert report["status"] == "complete"
    phases = [event["message"] for event in events]
    for phase in (
        "Lecture des preuves locales", "Inventaire local",
        "Comparaison des preuves locales",
        "Comparaison des identifiants bibliographiques",
        "Vérification des chemins enregistrés", "Audit local terminé",
    ):
        assert phase in phases
    assert any(event.get("current") == 1 and event.get("total") is None
               and event.get("message") == "Inventaire local" for event in events)
    encoded = json.dumps(events, ensure_ascii=False)
    assert "PRIVATE" not in encoded
    assert str(tmp_path) not in encoded
    assert "alice" not in encoded

