"""A stale Kobo acquisition result cannot survive a changed identity binding."""
from types import SimpleNamespace
from unittest.mock import patch

from cifereca.brokers import SynchronizationBrokerImpl


ACCOUNT = SimpleNamespace(
    identity_id="identity-a", provider_id="kobo", credential_id="credential",
    unique_id="account",
)
LAST_SUCCESS = "2026-10-15T10:00:00+00:00"


def broker(bound_identity):
    storage = SimpleNamespace(
        identity_for_connection=lambda provider, credential: bound_identity,
    )
    result = SynchronizationBrokerImpl(service=SimpleNamespace(storage=storage))
    result._materialization_state = lambda _source: {
        "status": "success", "last_attempt": LAST_SUCCESS,
        "last_success": LAST_SUCCESS,
    }
    return result


def test_rebound_identity_cannot_show_stale_acquisition_success():
    subject = broker("identity-b")
    with patch("cifereca.core.kobo_backup._kobo_checkpoint_for",
               side_effect=AssertionError("must not load another identity checkpoint")):
        phases = subject._phase_states(ACCOUNT, {}, ("local_materialization",))
    assert phases[1].status == "unknown"
    assert not phases[1].last_success
    assert phases[0].phase == "discover_rights"


def test_unbound_account_cannot_show_stale_acquisition_success():
    subject = broker(None)
    with patch("cifereca.core.kobo_backup._kobo_checkpoint_for",
               side_effect=AssertionError("must not load unbound checkpoint")):
        phases = subject._phase_states(ACCOUNT, {}, ("local_materialization",))
    assert phases[1].status == "unknown"
    assert not phases[1].last_success


def test_valid_binding_still_shows_explicit_success():
    subject = broker("identity-a")
    with patch("cifereca.core.kobo_backup._kobo_checkpoint_for", return_value=None):
        phases = subject._phase_states(ACCOUNT, {}, ("local_materialization",))
    assert phases[1].status == "success"
    assert phases[1].last_success == LAST_SUCCESS


def test_unsupported_provider_phase_remains_not_supported():
    subject = broker("identity-b")
    phases = subject._phase_states(ACCOUNT, {}, ())
    assert phases[1].status == "not_supported"
