"""Do not restart user-blocked Kobo acquisitions from the startup scheduler."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cifereca.core.kobo_backup import _kobo_backup_due


def service():
    return SimpleNamespace(
        config={"kobo_backup.interval_minutes": 60},
        identity_for_connection=lambda provider, credential: SimpleNamespace(identity_id="identity"),
        storage=SimpleNamespace(metadata_get=lambda key: None),
        _sessions={},
    )


@pytest.mark.parametrize("status", ["attention_required", "cancelled"])
def test_user_action_required_never_retries_automatically(status):
    with patch("cifereca.core.kobo_backup._kobo_checkpoint_for",
               return_value={"schema_version": 1, "status": status, "updated_at": 1}), \
         patch("cifereca.core.kobo_backup.time.time", return_value=20_000):
        assert _kobo_backup_due(service(), credential_id="credential") is False


def test_partial_checkpoint_can_retry_after_backoff():
    with patch("cifereca.core.kobo_backup._kobo_checkpoint_for",
               return_value={"schema_version": 1, "status": "partial", "updated_at": 1}), \
         patch("cifereca.core.kobo_backup.time.time", return_value=20_000):
        assert _kobo_backup_due(service(), credential_id="credential") is True


def test_interrupted_checkpoint_remains_restartable():
    with patch("cifereca.core.kobo_backup._kobo_checkpoint_for",
               return_value={"schema_version": 1, "status": "interrupted", "updated_at": 19_999}):
        assert _kobo_backup_due(service(), credential_id="credential") is True


def test_completed_recent_checkpoint_is_still_fresh():
    with patch("cifereca.core.kobo_backup._kobo_checkpoint_for",
               return_value={"schema_version": 1, "status": "complete", "updated_at": 19_999}), \
         patch("cifereca.core.kobo_backup.time.time", return_value=20_000):
        assert _kobo_backup_due(service(), credential_id="credential") is False
