"""#1345: Kobo audit presentation is bounded, privacy-safe and non-authorizing."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_view import sync_local_reconciliation_presentation
from tests.test_core_p0_1345_source_audit_broker import COUNTS


def _result(*, ok=True, **changes):
    facts = dict(
        phase="reconcile", status="audit_only", offline=True, read_only=True,
        acquisition_authorized=False, truncated=False, counts=dict(COUNTS),
    )
    facts.update(changes)
    return NS(ok=ok, facts=facts)


def test_valid_local_audit_presents_only_fixed_counters_and_non_authorization():
    result = _result(private="PRIVATE_HOME_PATH", cases=[{"sha": "PRIVATE_HASH"}])
    label = sync_local_reconciliation_presentation(result)
    assert "SHA : 3" in label
    assert "doublons binaires : 1" in label
    assert "empreintes divergentes : 1" in label
    assert "non indexés candidats : 1" in label
    assert "sans artefact confirmé : 2" in label
    assert "aucune réconciliation, acquisition ni suppression autorisée" in label.lower()
    assert "PRIVATE" not in label


def test_truncated_audit_is_explicitly_marked_partial():
    label = sync_local_reconciliation_presentation(_result(truncated=True))
    assert "Audit partiel" in label
    assert "terminé dans les limites" not in label
    assert "suppression" in label


@pytest.mark.parametrize("status,expected", [
    ("unknown_source", "Source introuvable"),
    ("not_supported", "réservé aux sources Kobo"),
    ("invalid_source", "incohérente"),
    ("ambiguous_source", "audit bloqué"),
    ("audit_unavailable", "indisponible"),
    ("PRIVATE_SECRET", "indisponible"),
])
def test_failed_audits_expose_only_fixed_message(status, expected):
    label = sync_local_reconciliation_presentation(
        _result(ok=False, status=status, warnings=("PRIVATE_TOKEN",)),
    )
    assert expected in label
    assert "PRIVATE" not in label
    assert "Aucun fichier modifié" in label


@pytest.mark.parametrize("changes", [
    {"read_only": False},
    {"offline": "yes"},
    {"acquisition_authorized": True},
    {"truncated": "false"},
    {"counts": {}},
    {"counts": {**COUNTS, "linked_files_verified": True}},
    {"counts": {**COUNTS, "binary_match_groups": -1}},
    {"counts": {**COUNTS, "active_entitlements_unmaterialized": 1_000_001}},
    {"counts": "PRIVATE"},
])
def test_malformed_ui_payload_always_shows_unavailable(changes):
    label = sync_local_reconciliation_presentation(_result(**changes))
    assert label == "Audit local indisponible. Aucun fichier modifié."


def test_non_mapping_receipt_is_not_interpreted():
    assert sync_local_reconciliation_presentation(NS(ok=True, facts="PRIVATE")) == (
        "Audit local indisponible. Aucun fichier modifié."
    )


def test_2498_shared_audit_limits_are_visible_without_claiming_missing_books():
    report = _result(
        truncated=True,
        files_hashed_limit=2048,
        directory_entries_limit=4096,
        total_hash_bytes_limit=64 * 1024 ** 3,
        total_hash_bytes_read=5 * 1024 ** 2,
        accounts=[{"account": "PRIVATE_ACCOUNT", "path": "/PRIVATE"}],
    )
    text = sync_local_reconciliation_presentation(report)
    assert "Audit partiel" in text
    assert "Plafonds partagés : 2048 vérifications SHA" in text
    assert "4096 entrées max." in text
    assert "5/65536 Mio de lecture cumulée" in text
    assert "PRIVATE" not in text and "/PRIVATE" not in text
    assert "Aucune réconciliation" in text


@pytest.mark.parametrize("changes", [
    {"files_hashed_limit": True},
    {"files_hashed_limit": 100_001},
    {"directory_entries_limit": "PRIVATE"},
    {"total_hash_bytes_limit": -1},
    {"total_hash_bytes_read": 10 ** 13},
    {"total_hash_bytes_read": "https://private.invalid?token=PRIVATE"},
])
def test_2498_malformed_or_hostile_budgets_are_not_interpolated(changes):
    defaults = dict(
        files_hashed_limit=2048, directory_entries_limit=4096,
        total_hash_bytes_limit=64 * 1024 ** 3,
        total_hash_bytes_read=0,
    )
    text = sync_local_reconciliation_presentation(_result(**(defaults | changes)))
    assert "Plafonds partagés" not in text
    assert "PRIVATE" not in text and "https://" not in text
    assert "Aucune réconciliation" in text
