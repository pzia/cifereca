"""Retired Kobo W2 startup scheduling cannot regain acquisition authority (#4858).

Historical retry metadata is retained for rollback/diagnostics only.
"""
from types import SimpleNamespace

import pytest

from cifereca.core.source_materialization_schedule import (
    mark_on_start_attempt, on_start_due, kobo_on_start_sessions,
    policy_key, retry_interval_key,
)


class PoisonStorage:
    def metadata_get(self, *_args):
        pytest.fail("retired W2 must not read the historical retry clock")

    def metadata_set(self, *_args):
        pytest.fail("retired W2 must not advance the historical retry clock")


ACCOUNT = SimpleNamespace(identity_id="identity", credential_id="credential")


@pytest.mark.parametrize("clock", [None, "6000", "9900", "invalid", "20000"])
@pytest.mark.parametrize("checkpoint_due", [True, False])
def test_retired_w2_schedule_is_never_due_or_persisted(clock, checkpoint_due):
    service = SimpleNamespace(
        storage=PoisonStorage(),
        kobo_backup_interval_minutes=lambda: 30,
        _kobo_backup_account_due=lambda *_args, **_kwargs: pytest.fail(
            "retired W2 must not inspect checkpoint eligibility"
        ),
    )
    assert on_start_due(service, ACCOUNT) is False
    assert mark_on_start_attempt(service, ACCOUNT) is False
    assert kobo_on_start_sessions(service) == ()


def test_retired_retry_policy_keys_remain_read_only_for_rollback():
    assert policy_key("source") == "sync.source.materialization.on_start.source"
    assert retry_interval_key("source") == "sync.source.materialization.retry_minutes.source"
