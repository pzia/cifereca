"""BnF Prêt #2055: the global-search detail must expose manual borrow.

The BnF provider intentionally marks the catalogue's availability UNKNOWN;
its user-verified borrow attempt must not be hidden by Paris' legacy text gate.
This is purely a UI candidate test, not authorization to send an HTTP POST.
"""
from dataclasses import replace
from pathlib import Path

from cifereca.gui.detail_model import (
    compact_availability_label, search_detail_borrow_action_candidate,
)
from cifereca.core.borrow_action_scope import borrow_claim_key
from tests.test_core_p0_2055_manual_dispatch import setup


def test_bnf_unknown_availability_does_not_hide_manual_action(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        # This is exactly the BnF provider's default detail-label projection.
        availability = compact_availability_label("Disponibilité non vérifiée")
        assert availability == "Non vérifiée"
        assert search_detail_borrow_action_candidate(item, availability)
        # Real capability/identity/session/claim validation remains separate.
        assert service.can_borrow_bnf(item)
        assert session.reads == session.dispatches == []
    finally:
        service.close()


def test_bnf_candidate_remains_advisory_when_attempt_is_spent(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        key = borrow_claim_key(account, item)
        assert key and service.can_borrow_bnf(item)
        service.storage.metadata_set(key, "unreadable-spent-checkpoint")
        availability = compact_availability_label("Disponibilité non vérifiée")
        assert search_detail_borrow_action_candidate(item, availability)
        assert not service.can_borrow_bnf(item)
        assert session.reads == session.dispatches == []
    finally:
        service.close()


def test_bnf_only_exception_preserves_other_provider_presentation_rules(tmp_path):
    service, session, account, item = setup(tmp_path)
    try:
        unknown = compact_availability_label("Disponibilité non vérifiée")
        assert search_detail_borrow_action_candidate(
            replace(item, record_id=""), unknown) is False
        assert search_detail_borrow_action_candidate(
            replace(item, docbase="OTHER"), unknown) is False
        assert search_detail_borrow_action_candidate(
            replace(item, medium="physical"), unknown) is False
        for source in ("kobo", "numilog", "numilog-catalog"):
            assert not search_detail_borrow_action_candidate(
                replace(item, source_provider=source), "Empruntable")
        assert not search_detail_borrow_action_candidate(
            replace(item, source_provider="paris", metadata={}), unknown)
        assert search_detail_borrow_action_candidate(
            replace(item, source_provider="paris", metadata={}), "Empruntable")
        assert not search_detail_borrow_action_candidate(object(), "Empruntable")
        assert session.reads == session.dispatches == []
    finally:
        service.close()


def test_search_detail_actual_ui_uses_candidate_and_core_admission():
    """The GTK renderer must not reintroduce the legacy string-only veto."""
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"
    source = path.read_text(encoding="utf-8")
    assert "elif pending_followup or search_detail_borrow_action_candidate(item, availability):" in source
    assert "if not pending_followup and self.window.can_borrow_item(item):" in source
    assert "self.window.borrow_search_item(item, borrow)" in source
    assert 'availability == "Empruntable"' not in source
