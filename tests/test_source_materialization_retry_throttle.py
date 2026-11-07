"""Regression tests for Kobo per-source startup retry throttling (#1346)."""
from types import SimpleNamespace
from unittest.mock import patch

from cifereca.core.source_materialization_schedule import on_start_due


class Storage:
    def __init__(self, clock):
        self.clock = clock

    def metadata_get(self, key):
        if key.startswith("sync.source.materialization.retry_minutes."):
            return None  # Compatibility path before the first scoped attempt.
        if (key.startswith("sync.source.materialization.attempt.v1.")
                or key.startswith("kobo.backup.checkpoint.")):
            return None
        assert key.startswith("sync.source.materialization.last_attempt.")
        return self.clock


def service(clock, checkpoint_due=True):
    return SimpleNamespace(
        storage=Storage(clock),
        kobo_backup_interval_minutes=lambda: 30,
        _kobo_backup_account_due=lambda credential, *, interval_minutes=None: checkpoint_due,
    )


ACCOUNT = SimpleNamespace(identity_id="identity", credential_id="credential",
                          provider_id="kobo", unique_id="borrower")


def test_checkpoint_does_not_bypass_recent_attempt():
    with patch("cifereca.core.kobo_backup._kobo_checkpoint_for", return_value=object()), \
         patch("cifereca.core.source_materialization_schedule._account_id", return_value="source"), \
         patch("cifereca.core.source_materialization_schedule.time.time", return_value=10_000):
        assert on_start_due(service("9900"), ACCOUNT) is False


def test_old_checkpoint_never_authorizes_an_automatic_second_get():
    with patch("cifereca.core.kobo_backup._kobo_checkpoint_for", return_value=object()), \
         patch("cifereca.core.source_materialization_schedule._account_id", return_value="source"), \
         patch("cifereca.core.source_materialization_schedule.time.time", return_value=10_000):
        # #3198: a legacy 30-minute preference no longer shortens the
        # conservative 60-minute minimum for source-scoped startup retries.
        assert on_start_due(service("8000"), ACCOUNT) is False
        assert on_start_due(service("6500"), ACCOUNT) is False
        assert on_start_due(service("6000"), ACCOUNT) is False
        assert on_start_due(service("6000", checkpoint_due=False), ACCOUNT) is False


def test_no_checkpoint_first_attempt_remains_due():
    with patch("cifereca.core.kobo_backup._kobo_checkpoint_for", return_value=None), \
         patch("cifereca.core.source_materialization_schedule._account_id", return_value="source"), \
         patch("cifereca.core.source_materialization_schedule.time.time", return_value=10_000):
        assert on_start_due(service(None), ACCOUNT) is True
        assert on_start_due(service("9900"), ACCOUNT) is False


def test_corrupt_or_future_clock_fails_closed_with_checkpoint():
    with patch("cifereca.core.kobo_backup._kobo_checkpoint_for", return_value=object()), \
         patch("cifereca.core.source_materialization_schedule._account_id", return_value="source"), \
         patch("cifereca.core.source_materialization_schedule.time.time", return_value=10_000):
        assert on_start_due(service("invalid"), ACCOUNT) is False
        assert on_start_due(service("20000"), ACCOUNT) is False
