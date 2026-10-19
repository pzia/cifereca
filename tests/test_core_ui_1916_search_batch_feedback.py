"""#1916: source status remains visible, closed and request-local."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.search_batch_feedback import (
    public_batch_status, safe_source_statuses,
)
from tests.test_search_controller_state import _host as controller_host
from tests.test_search_results_view import _host as view_host


def test_untrusted_metadata_never_enters_source_caption_or_tooltip():
    statuses = {
        "https://user.invalid/?token=SECRET_1916": "known_catalog_stale",
        "alice@example.invalid": "access_denied",
        "account-home-dir": "unavailable",
        "unknown": "https://bad.invalid/PRIVATE_1916",
    }
    label, tooltip = safe_source_statuses(statuses)
    assert label.startswith(" · 1 source secours local · 1 source accès refusé")
    assert "indisponible" in tooltip
    for private in ("SECRET_1916", "PRIVATE_1916", "example.invalid", "account-home-dir"):
        assert private not in label + tooltip


def test_auth_refusal_is_not_described_as_a_transport_outage():
    assert public_batch_status(NS(result_status="unavailable",
                                  error_code="SearchAuthenticationRequired")) == "authentication_required"
    assert public_batch_status(NS(result_status="unavailable",
                                  error_code="SearchAccessDenied")) == "access_denied"
    assert public_batch_status(NS(result_status="unavailable",
                                  error_code="SearchTemporarilyUnavailable")) == "unavailable"
    assert public_batch_status(NS(result_status="known_catalog_stale",
                                  error_code="SearchTemporarilyUnavailable")) == "known_catalog_stale"
    assert public_batch_status(NS(result_status="PRIVATE_URL", error_code="x")) is None
    assert public_batch_status(NS(result_status=True, error_code="x")) is None


def test_distinguishes_successful_live_from_cached_and_truncated_mixed_states():
    assert safe_source_statuses({"a": "live", "b": "live"}) == (
        " · 2 sources en ligne", "Recherche par source : 2 sources en ligne"
    )
    assert safe_source_statuses({"a": "cached"})[0] == " · 1 source en cache"
    label, tooltip = safe_source_statuses(
        {"a": "known_catalog_stale", "b": "unavailable", "c": "partial",
         "d": "cached", "e": "live"}
    )
    assert "autres états" in label
    assert "en cache" in tooltip and "résultats partiels" in tooltip
    assert len(label) < len(tooltip)


def test_batch_callbacks_do_not_resurrect_stale_generation_or_identity():
    host = controller_host()
    host.run_search(True)
    old_request = host.requests[-1]
    old_request.work()
    old_on_batch = host.online.call_args.args[2]
    old_on_batch(NS(provider_id="public", result_status="unavailable",
                    error_code="SearchTemporarilyUnavailable", results=[]))
    # Switch identities before GTK schedules a delivery: reject the status.
    host.service.current_identity = NS(identity_id="bob")
    callback, args = host.scheduled.pop()
    assert callback(*args) is False
    assert host._online_source_statuses == {}
    host.service.current_identity = NS(identity_id="alice")

    host.run_search(True)
    new_request = host.requests[-1]
    new_request.work()
    new_on_batch = host.online.call_args.args[2]
    new_on_batch(NS(provider_id="public", result_status="known_catalog_stale",
                    error_code="SearchTemporarilyUnavailable", results=[]))
    callback, args = host.scheduled.pop()
    assert callback(*args) is False
    assert host._online_source_statuses == {"public": "known_catalog_stale"}
    old_request.done(NS(warnings=["old-private-warning"]))
    assert host._online_source_statuses == {"public": "known_catalog_stale"}
    host.run_search(False)
    assert host._online_source_statuses == {}
    assert host._online_source_context is None


def test_rendered_count_persists_distinct_closed_statuses_only_in_matching_context():
    host = view_host()
    host._search_mode = "global"
    host._online_source_statuses = {
        "private@example.invalid": "unavailable",
        "public": "known_catalog_stale",
    }
    host._online_source_context = ("", "all", "all", "alice")
    host._update_search_count(0)
    assert host.search_page.count_label.set_label.call_args.args[0] == (
        "0 résultats · Étendu · 1 source secours local · 1 source indisponible"
    )
    host._online_source_context = ("old-query", "all", "all", "alice")
    host._update_search_count(0)
    assert host.search_page.count_label.set_label.call_args.args[0] == "0 résultats · Étendu"
    host._search_mode = "local"
    host._update_search_count(1)
    assert host.search_page.count_label.set_label.call_args.args[0] == "1 résultat · Connu"
