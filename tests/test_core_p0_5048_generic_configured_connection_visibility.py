"""#5048: exact, provider-neutral configured connection visibility.

No provider access, rights, download or account creation. All examples
use synthetic accounts and a local inert configuration snapshot.
"""
from types import SimpleNamespace as NS

from cifereca.core.sync_connection_visibility import (
    unavailable_configured_connections,
)


def _service(rows, live=(), *, provider_ready=True):
    return NS(
        config=NS(provider_accounts=lambda _provider: list(rows)),
        accounts_for_identity=lambda _identity: list(live),
        registry=NS(maybe_get=lambda _provider: (
            NS(descriptor=NS(state="ready")) if provider_ready else None
        )),
        _provider_enabled_for_identity=lambda _provider, _identity: True,
    )


def _row(provider, identity, credential):
    return {
        "provider": provider, "identity_id": identity, "id": credential,
        "enabled": True, "label": "PRIVATE SOURCE", "credentials": {
            "password": "PRIVATE PASSWORD",
        },
    }


def test_exact_provider_and_identity_isolation_without_private_identifiers():
    rows = [_row("alpha-library", "alice", "a"), _row("beta-library", "alice", "b"),
            _row("alpha-library", "bob", "c")]
    service = _service(rows)
    notices = unavailable_configured_connections(
        service, "alice", provider_id="alpha-library",
    )
    assert [(n.provider_id, n.state, n.can_reconnect) for n in notices] == [
        ("alpha-library", "not_connected", True),
    ]
    assert "PRIVATE" not in repr(notices)
    assert "alice" not in repr(notices)
    assert "a" not in repr(notices)


def test_connected_account_only_hides_exact_provider_credential_and_identity():
    rows = [_row("alpha-library", "alice", "shared"),
            _row("beta-library", "alice", "shared")]
    live = [NS(provider_id="beta-library", identity_id="alice",
               credential_id="shared")]
    service = _service(rows, live)
    assert len(unavailable_configured_connections(
        service, "alice", provider_id="alpha-library",
    )) == 1
    assert unavailable_configured_connections(
        service, "alice", provider_id="beta-library",
    ) == ()


def test_invalid_scope_missing_plugin_and_hostile_readers_fail_closed():
    service = _service([_row("alpha-library", "alice", "a")], provider_ready=False)
    assert unavailable_configured_connections(
        service, "alice", provider_id="alpha-library",
    )[0].state == "plugin_missing"
    for invalid in ("", "ALPHA", "a/b", "a" * 65, None, 1):
        assert unavailable_configured_connections(
            service, "alice", provider_id=invalid,
        ) == ()
    service.config.provider_accounts = lambda _id: (_ for _ in ()).throw(
        RuntimeError("PRIVATE_ACCESS_TOKEN"))
    assert unavailable_configured_connections(
        service, "alice", provider_id="alpha-library",
    ) == ()
