from copy import deepcopy
from types import SimpleNamespace

import pytest

from cifereca.core.provider_sessions import configured_connection_observation
from cifereca.models import Account
from cifereca.providers.base import ProviderSession


class Session(ProviderSession):
    def connect(self):
        raise AssertionError("unexpected provider call")

    def close(self):
        raise AssertionError("unexpected provider call")


def fixture():
    row = dict(id="one", provider="synthetic", identity_id="alice",
               enabled=True, credentials={"token": "synthetic"}, options={})
    session = Session(descriptor=SimpleNamespace(id="synthetic", display_name="Synthetic"),
                      credential_id="one", label="Local",
                      credentials=row["credentials"], options={})
    session.connected = True
    account = Account(provider_id="synthetic", credential_id="one", identity_id="alice")
    core = SimpleNamespace(config=SimpleNamespace(provider_accounts=lambda **kw: [deepcopy(row)]),
                           _session_key=lambda p, c: p + "::" + c,
                           _sessions={"synthetic::one": session}, accounts=[account])
    return core, row, session


@pytest.mark.parametrize("connected,reason,expected", [
    (True, None, "connected"),
    (False, "session_expired", "expired"),
    (False, None, "disconnected"),
])
def test_observed_state(connected, reason, expected):
    core, row, session = fixture()
    session.connected = connected
    session.sync_reconnect_reason = reason
    assert configured_connection_observation(core, row) == expected


@pytest.mark.parametrize("change", ["owner", "credentials", "options", "disabled", "duplicate", "missing"])
def test_stale_scope_is_unknown(change):
    core, row, session = fixture()
    if change == "owner":
        core.accounts[0].identity_id = "bob"
    elif change == "credentials":
        session.credentials = {"token": "old"}
    elif change == "options":
        session.options = {"old": "value"}
    elif change == "disabled":
        row["enabled"] = False
    elif change == "duplicate":
        core.config.provider_accounts = lambda **kw: [row, row]
    else:
        core._sessions.clear()
    assert configured_connection_observation(core, row) == "unknown"
