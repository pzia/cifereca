"""P0 #1143: plugin-defined exception names never enter virtual-provider logs."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core import provider_sessions


PRIVATE = "PRIVATE_ACCOUNT_TOKEN_2026"


class HostileError(Exception):
    def __str__(self):
        raise AssertionError("exception text must not be formatted")

    def __repr__(self):
        raise AssertionError("exception must not be repr'd")


HostileError.__name__ = PRIVATE


class Diagnostics:
    def __init__(self):
        self.entries = []

    def add(self, category, title, status, *, detail, **kwargs):
        self.entries.append((category, title, status, detail))


def _fail(*args, **kwargs):
    raise HostileError("https://private.invalid/?token=" + PRIVATE)


def test_virtual_session_connection_failure_has_only_closed_code():
    diagnostics = Diagnostics()
    session = NS(provider_id="virtual", connect=_fail, close=lambda: None)
    plugin = NS(
        descriptor=NS(display_name="Virtual"),
        create_session=lambda **kwargs: session,
    )
    service = NS(
        registry=NS(maybe_get=lambda name: plugin),
        _session_key=lambda provider, credential: provider + ":" + credential,
        _sessions={}, _session_errors={},
        config=object(), storage=object(), diagnostics=diagnostics,
    )
    provider_sessions._connect_changed_virtual_provider(service, "virtual")
    assert service._sessions == {}
    assert service._session_errors == {"virtual:__public__": PRIVATE} is False
    assert diagnostics.entries == [
        ("PLUGIN", "virtual provider connection", "error",
         {"provider": "virtual", "code": "virtual_provider_connect_failed"})
    ]
    assert PRIVATE not in repr(diagnostics.entries) + repr(service._session_errors)


def test_virtual_subscription_failure_uses_closed_code():
    diagnostics = Diagnostics()
    service = NS(
        _virtual_parent_subscriptions={},
        _queue_virtual_collection_refresh=lambda: None,
        diagnostics=diagnostics,
    )
    provider_sessions._sync_virtual_subscriptions(
        service, {"virtual": NS(subscribe=_fail)},
    )
    assert diagnostics.entries == [
        ("PLUGIN", "virtual inventory subscription", "error",
         {"parent": "virtual", "code": "virtual_inventory_subscription_failed"})
    ]
    assert PRIVATE not in repr(diagnostics.entries)


def test_virtual_notification_failure_uses_closed_code():
    diagnostics = Diagnostics()
    class Lock:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
    service = NS(
        _virtual_schedule_lock=Lock(), _virtual_collection_closed=False,
        _virtual_refresh_pending=True, _virtual_refresh_again=False,
        refresh_virtual_collections=_fail, diagnostics=diagnostics,
    )
    provider_sessions._drain_virtual_collection_events(service)
    assert service._virtual_refresh_pending is False
    assert diagnostics.entries == [
        ("PLUGIN", "virtual notification", "error",
         {"code": "virtual_notification_failed"})
    ]
    assert PRIVATE not in repr(diagnostics.entries)
