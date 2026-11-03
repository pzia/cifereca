"""#4281: distinguish configured provider connect cost from Core registration.

No Paris network, actual account, GTK or filesystem writes. Observed
durations are deterministic synthetic wall times, not supplier benchmarks.
"""
from __future__ import annotations

import logging
import re

from cifereca.core import provider_sessions
from tests.test_core_p0_1143_provider_connect_privacy import (
    _rig, SECRET,
)


_PHASE = re.compile(
    r"\AEVENT provider connect_phase "
    r"operation_ref=([0-9a-f]{24}) provider_id=provider\.test "
    r"phase=(create_session|session_connect|account_registration) "
    r"outcome=(returned|raised) duration_ms=(\d+)\Z"
)


def _events(caplog):
    rows = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("EVENT provider connect_phase ")
    ]
    matches = [_PHASE.fullmatch(row) for row in rows]
    assert all(match is not None for match in matches), rows
    return [(m.group(1), m.group(2), m.group(3), int(m.group(4)))
            for m in matches]


def test_configured_connection_reports_three_scoped_separate_costs(
    caplog, monkeypatch,
):
    state, session, plugin = _rig(succeeds=True)
    clock = [100.0]
    monkeypatch.setattr(provider_sessions.time, "monotonic", lambda: clock[0])

    def build(**_kwargs):
        clock[0] += 0.003
        return session

    def connect():
        clock[0] += 0.100
        session.connected = True
        return [state.storage and session.connect_account]

    def register_connection(_row):
        clock[0] += 0.007
        return 1

    def register_account(_row):
        clock[0] += 0.013

    # The fake borrower has the exact credential/identity coordinates used by
    # the original privacy rig. We never log them.
    from types import SimpleNamespace as NS
    session.connect_account = NS(
        provider_id="provider.test", credential_id=SECRET,
        identity_id="identity-private", unique_id=SECRET,
        display_name="Private Label", is_primary=True, barcode=SECRET,
    )
    plugin.create_session.side_effect = build
    session.connect.side_effect = connect
    state.storage.provider_connection_put = register_connection
    state.storage.provider_account_put.side_effect = register_account

    with caplog.at_level(logging.INFO, logger="cifereca"):
        result = provider_sessions.connect_configured(
            state, logger=logging.getLogger("cifereca"),
        )
    assert len(result["connected"]) == 1 and not result["errors"]
    events = _events(caplog)
    assert len(events) == 3
    refs = {row[0] for row in events}
    assert len(refs) == 1
    assert [(p, outcome) for _, p, outcome, _ in events] == [
        ("create_session", "returned"),
        ("session_connect", "returned"),
        ("account_registration", "returned"),
    ]
    assert all(abs(got - expected) <= 1 for got, expected in
               zip((ms for _, _, _, ms in events), (3, 100, 20)))
    for secret in (SECRET, "identity-private", "Private Label", "password"):
        assert secret not in caplog.text


def test_failed_provider_connect_times_only_started_stages_without_retry(
    caplog,
):
    state, session, plugin = _rig(succeeds=False)
    with caplog.at_level(logging.INFO, logger="cifereca"):
        result = provider_sessions.connect_configured(
            state, logger=logging.getLogger("cifereca"),
        )
    assert len(result["connected"]) == 0
    session.connect.assert_called_once()
    assert state.storage.provider_account_put.call_count == 0
    events = _events(caplog)
    assert [p for _, p, _, _ in events] == [
        "create_session", "session_connect",
    ]
    assert events[0][2] == "returned"
    assert events[1][2] == "raised"
    assert events[0][0] == events[1][0]
    assert "account_registration" not in caplog.text
    assert SECRET not in caplog.text


def test_failed_account_registration_does_not_relabel_successful_connect(
    caplog,
):
    state, session, _ = _rig(succeeds=True)
    state.storage.provider_account_put.side_effect = RuntimeError(
        "PRIVATE_ACCOUNT_CREDENTIAL"
    )
    with caplog.at_level(logging.INFO, logger="cifereca"):
        result = provider_sessions.connect_configured(
            state, logger=logging.getLogger("cifereca"),
        )
    assert result["errors"]
    events = _events(caplog)
    assert [(p, outcome) for _, p, outcome, _ in events] == [
        ("create_session", "returned"),
        ("session_connect", "returned"),
        ("account_registration", "raised"),
    ]
    assert SECRET not in caplog.text
