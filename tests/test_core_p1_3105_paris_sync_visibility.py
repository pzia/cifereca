"""#3105: configured Paris stays visible but never becomes a fake SyncSource."""
from types import SimpleNamespace as NS

from cifereca.core.sync_connection_visibility import unavailable_paris_connections


def _service(configured, accounts=(), *, plugin="ready", enabled=True):
    def lookup(_id):
        if plugin is None:
            return None
        return NS(descriptor=NS(state=plugin))
    return NS(
        config=NS(provider_accounts=lambda provider: configured if provider == "paris" else []),
        registry=NS(maybe_get=lookup),
        accounts_for_identity=lambda identity: list(accounts),
        _provider_enabled_for_identity=lambda provider, identity: enabled,
    )


def _row(id="p1", identity="alice", **extra):
    return {"provider": "paris", "identity_id": identity, "id": id, "enabled": True, **extra}


def test_failed_paris_and_connected_bnf_still_show_a_non_actionable_notice():
    bnf = NS(provider_id="bnf-pret", identity_id="alice", credential_id="bnf")
    notices = unavailable_paris_connections(_service([_row()], [bnf]), "alice")
    assert [(n.provider_id, n.state, n.can_reconnect) for n in notices] == [
        ("paris", "not_connected", True)
    ]
    assert not hasattr(notices[0], "account_id")
    assert not hasattr(notices[0], "source_id")


def test_connected_primary_and_linked_account_do_not_create_phantom_notice():
    paris = NS(provider_id="paris", identity_id="alice", credential_id="p1")
    assert unavailable_paris_connections(_service([_row()], [paris]), "alice") == ()
    # All linked borrower accounts belong to one credential/session; a single
    # real primary account is enough to avoid a duplicate connection notice.
    linked = NS(provider_id="paris", identity_id="alice", credential_id="p1")
    assert unavailable_paris_connections(_service([_row()], [paris, linked]), "alice") == ()


def test_two_paris_credentials_only_warn_for_the_one_not_connected():
    live = NS(provider_id="paris", identity_id="alice", credential_id="p1")
    notices = unavailable_paris_connections(_service([_row(), _row("p2")], [live]), "alice")
    assert len(notices) == 1 and notices[0].state == "not_connected"


def test_identity_isolation_even_with_a_misleading_account_reader():
    wrong = NS(provider_id="paris", identity_id="bob", credential_id="p1")
    service = _service([_row("p1", "bob")], [wrong])
    assert unavailable_paris_connections(service, "alice") == ()
    # A configuration owned by Alice cannot be hidden by Bob's account.
    service = _service([_row("p1", "alice")], [wrong])
    assert unavailable_paris_connections(service, "alice")[0].state == "not_connected"
    assert unavailable_paris_connections(service, "") == ()


def test_disabled_plugin_missing_plugin_disabled_account_are_not_retryable():
    cases = [
        (_service([_row()], plugin=None), "plugin_missing"),
        (_service([_row()], plugin="skeleton"), "plugin_unavailable"),
        (_service([_row()], enabled=False), "plugin_disabled"),
        (_service([_row(enabled=False)]), "account_disabled"),
        (_service([_row(id="")]), "invalid_configuration"),
    ]
    for service, expected in cases:
        notice, = unavailable_paris_connections(service, "alice")
        assert notice.state == expected and notice.can_reconnect is False


def test_unbound_legacy_accounts_and_bad_projections_never_cross_identities():
    service = _service([{"provider": "paris", "id": "legacy", "enabled": True}])
    assert unavailable_paris_connections(service, "alice") == ()
    assert unavailable_paris_connections(_service([_row()] * 129), "alice") == ()
    service.config.provider_accounts = lambda _: (_ for _ in ()).throw(RuntimeError("token=secret"))
    assert unavailable_paris_connections(service, "alice") == ()


def test_notice_is_bounded_and_contains_no_private_labels():
    rows = [
        {**_row(str(i)), "label": "account-secret", "credentials": {"password": "private"}}
        for i in range(50)
    ]
    notices = unavailable_paris_connections(_service(rows), "alice")
    assert len(notices) == 16
    assert "secret" not in repr(notices)
    assert "private" not in repr(notices)
