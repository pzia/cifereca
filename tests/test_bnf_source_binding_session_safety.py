"""#5055: BnF provider-session guard regressions kept outside neutral Core tests."""
import pytest
from cifereca.core.source_account_binding import source_account_binding_ready
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core  # noqa: F401

def test_live_session_getter_is_never_read_at_binding_boundary(
    connected_core, monkeypatch,
):
    """An untrusted plugin property may not execute while proving identity."""
    service, session, account, _loan, _state, downloaded = connected_core
    assert source_account_binding_ready(
        service, account, expected_provider_id="bnf-pret",
    )
    reads = []

    def hostile(_session):
        reads.append("untrusted-provider-getter")
        raise RuntimeError("private provider side effect")

    monkeypatch.setattr(type(session), "accounts", property(hostile))
    assert not source_account_binding_ready(
        service, account, expected_provider_id="bnf-pret",
    )
    assert reads == []
    assert downloaded == []


def test_live_session_scalar_property_cannot_forge_connected(
    connected_core, monkeypatch,
):
    service, session, account, _loan, _state, downloaded = connected_core
    reads = []

    def hostile(_session):
        reads.append("unexpected")
        return True

    monkeypatch.setattr(type(session), "connected", property(hostile), raising=False)
    assert not source_account_binding_ready(
        service, account, expected_provider_id="bnf-pret",
    )
    assert reads == [] and downloaded == []


def test_closed_provider_session_cannot_retain_source_binding(connected_core):
    service, session, account, _loan, _state, downloaded = connected_core
    assert source_account_binding_ready(
        service, account, expected_provider_id="bnf-pret",
    )
    original_closed = session._closed
    try:
        # A stale connected scalar must not revive a closed provider session.
        session._closed = True
        session.connected = True
        assert not source_account_binding_ready(
            service, account, expected_provider_id="bnf-pret",
        )
        assert downloaded == []
    finally:
        session._closed = original_closed
