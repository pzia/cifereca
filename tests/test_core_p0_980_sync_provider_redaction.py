"""Regression: the Sync page must not echo arbitrary provider diagnostics."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca.gui.sync_view import sync_source_presentation


def _source(**updates):
    values = dict(
        provider_id="paris", label="Bibliothèque", enabled=True,
        connected=True, sync_capable=True, status="error", error="",
        last_attempt="", last_success="",
    )
    values.update(updates)
    return SimpleNamespace(**values)


@pytest.mark.parametrize("secret", [
    "https://private.example/books?access_token=SECRET",
    "/home/user/.config/cifereca/private-credential.json",
    "account_id=PRIVATE_ACCOUNT",
    "Authorization: Bearer PRIVATE_TOKEN",
])
def test_provider_error_is_reduced_to_generic_failure(secret):
    presentation = sync_source_presentation(_source(error=secret))
    assert presentation.sync_label == "Échec"
    assert "Synchronisation : Échec" in presentation.summary
    assert secret not in presentation.summary
    assert presentation.action_enabled is True


@pytest.mark.parametrize("secret", [
    "https://private.example/token?secret=VALUE",
    "2026-13-99 PRIVATE_TOKEN",
    "/home/private/sync.txt",
])
def test_malformed_provider_timestamps_are_not_echoed(secret):
    presentation = sync_source_presentation(_source(
        status="idle", last_attempt=secret, last_success=secret,
    ))
    assert presentation.sync_label == "Jamais synchronisée"
    assert secret not in presentation.summary


def test_valid_last_success_and_partial_status_remain_visible():
    presentation = sync_source_presentation(_source(
        status="partial", error="",
        last_success="2026-10-14T08:30:00+00:00",
        last_attempt="2026-10-15T08:00:00+00:00",
    ))
    assert "Partielle · 15/10/2026 08:00" in presentation.summary
    assert "Dernier succès confirmé : 14/10/2026 08:30" in presentation.summary


def test_non_failure_empty_status_keeps_no_sync_history():
    presentation = sync_source_presentation(_source(status="idle", error=""))
    assert presentation.sync_label == "Jamais synchronisée"
