"""#2370: per-source connection failures are attributable without account data."""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace as NS

import pytest

from cifereca.core import provider_sessions
from cifereca.core.diagnostic_event_feed import _source_diagnostics
from cifereca.core.public_provider_diagnostics import (
    UNKNOWN_PROVIDER, bounded_connection_failures, canonical_public_provider_id,
    registered_public_provider_id,
)
from cifereca.diagnostics import Diagnostics
from tests.test_core_p0_1143_provider_connect_privacy import (
    SECRET, SIGNED, _rig,
)


LOGGER = logging.getLogger("cifereca")


@pytest.mark.parametrize("singleton", [False, True])
def test_failure_has_canonical_public_id_in_outcome_diagnostics_and_log(
    singleton, caplog,
):
    service, session, _ = _rig(singleton=singleton)
    with caplog.at_level(logging.INFO, logger="cifereca"):
        result = provider_sessions.connect_configured(service, logger=LOGGER)
    assert len(result["errors"]) == 1
    assert result["connection_failures"] == {
        "items": ({"provider_id": "provider.test", "code": "provider_connect_failed"},),
        "truncated": 0,
    }
    diagnostic = provider_sessions.provider_diagnostic_state(service)
    assert diagnostic["errors"] == {"connect_failed": 1}
    assert diagnostic["connection_failures"] == result["connection_failures"]
    event_rows, coverage = _source_diagnostics(service.diagnostics)
    failures = [r for r in event_rows if r.get("detail", {}).get("code")
                == "provider_connect_failed"]
    assert len(failures) == 1
    assert failures[0]["detail"] == result["connection_failures"]["items"][0]
    assert failures[0]["message"] == "connexion fournisseur échouée"
    assert coverage["status"] == "available"
    assert "provider_id=provider.test" in caplog.text
    publication = json.dumps(result["connection_failures"]) + json.dumps(diagnostic)
    publication += json.dumps(event_rows) + caplog.text + service.diagnostics.text("core")
    for secret in (SECRET, "SECRET_TOKEN", "private.invalid",
                   "HostileFailure", "identity-private", "Private label"):
        assert secret not in publication


def test_two_failing_accounts_keep_two_records_same_canonical_source_without_ids(
    caplog,
):
    service, session, _ = _rig()
    service.config.provider_accounts = lambda **kw: [
        {"id": SECRET, "provider": "provider.test",
         "identity_id": "private-identity", "credentials": {"password": SECRET}},
        {"id": "private-other-account", "provider": "provider.test",
         "identity_id": "private-identity", "credentials": {"password": SECRET}},
    ]
    with caplog.at_level(logging.INFO, logger="cifereca"):
        result = provider_sessions.connect_configured(service, logger=LOGGER)
    assert len(result["errors"]) == 2
    assert session.connect.call_count == 2
    assert [r["provider_id"] for r in result["connection_failures"]["items"]] == [
        "provider.test", "provider.test",
    ]
    diag = provider_sessions.provider_diagnostic_state(service)
    assert diag["errors"]["connect_failed"] == 2
    assert len(diag["connection_failures"]["items"]) == 2
    events, _ = _source_diagnostics(service.diagnostics)
    assert len([e for e in events if e.get("detail", {}).get("provider_id")
                == "provider.test"]) == 2
    exported = json.dumps(diag) + json.dumps(events) + caplog.text
    assert SECRET not in exported
    assert "private-other-account" not in exported
    assert "private-identity" not in exported


@pytest.mark.parametrize("provider", [
    "User@example.com", "provider.bad/child", "provider.bad\\secret",
    "provider.évil", "provider.token", "provider.account",
    "A" * 140, "https://reader.invalid/?token=SECRET",
    "kobo\ncredential=SECRET", "provider.0123",
])
def test_canonical_slug_denylist_never_exports_untrusted_provider(provider):
    assert canonical_public_provider_id(provider) is None
    service, session, plugin = _rig()
    plugin.descriptor.id = provider
    service.config.provider_accounts = lambda **kw: [{
        "id": SECRET, "provider": provider, "credentials": {"token": SECRET},
    }]
    public = provider_sessions.connect_configured(service, logger=LOGGER)[
        "connection_failures"
    ]
    assert public["items"] == ({
        "provider_id": UNKNOWN_PROVIDER,
        "code": "provider_connect_failed",
    },)
    assert SECRET not in str(public)


def test_unregistered_provider_reports_unknown_without_leaking_config_slug():
    service, session, _ = _rig()
    service.registry.maybe_get = lambda _id: None
    result = provider_sessions.connect_configured(service, logger=LOGGER)
    assert len(result["errors"]) == 1
    assert result["connection_failures"]["items"] == ({
        "provider_id": UNKNOWN_PROVIDER, "code": "provider_not_registered",
    },)
    assert "provider.test" not in json.dumps(result["connection_failures"])


def test_reconnect_clears_previous_errors_and_no_fabricated_success():
    service, session, _ = _rig()
    provider_sessions.connect_configured(service, logger=LOGGER)
    session.connect.side_effect = None
    session.connect.return_value = [NS(
        provider_id="provider.test", credential_id=SECRET, unique_id=SECRET,
        identity_id="identity-private", display_name="Private", is_primary=True,
        barcode=SECRET,
    )]
    # A real successful reconnect commits its session state as well as rows.
    session.connect.side_effect = lambda: (setattr(session, "connected", True), session.connect.return_value)[1]
    second = provider_sessions.connect_configured(service, logger=LOGGER)
    assert len(second["connected"]) == 1
    assert second["errors"] == {}
    assert second["connection_failures"] == {"items": (), "truncated": 0}
    assert provider_sessions.provider_diagnostic_state(service)["connection_failures"][
        "items"
    ] == ()


def test_untrusted_diagnostics_details_are_not_public_provider_ids():
    diagnostic = Diagnostics(level="verbose")
    for detail in [
        {"provider_id": "malicious.invalid/LOAN/secret", "code": "provider_connect_failed"},
        {"provider_id": "provider.test", "code": "PRIVATE_UNTRUSTED_CODE"},
        {"provider_id": SIGNED, "code": "provider_connect_failed"},
    ]:
        diagnostic.add("PLUGIN", "provider connect", "error", detail=detail)
    diagnostic.add("PLUGIN", "arbitrary operation", "error",
                   detail={"provider_id": "provider.test",
                           "code": "provider_connect_failed"})
    rows, _ = _source_diagnostics(diagnostic)
    assert len(rows) == 4
    assert not any(r.get("detail") for r in rows)
    assert "private.invalid" not in json.dumps(rows)
    assert "malicious" not in json.dumps(rows)


def test_projection_bounded_and_does_not_read_private_keys():
    entries = [{"provider_id": "provider.test", "code": "provider_connect_failed",
                "credential_id": SECRET, "identity_id": SECRET}] * 48
    result = bounded_connection_failures(entries)
    assert len(result["items"]) == 40
    assert result["truncated"] == 8
    assert SECRET not in repr(result)
    assert registered_public_provider_id(NS(maybe_get=lambda _slug: None),
                                         "provider.test") == UNKNOWN_PROVIDER
