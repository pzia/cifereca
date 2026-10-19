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
    # Explicit local search starts the next generation, clearing pending key.
    host.run_search(False)
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


def test_empty_failed_source_is_visible_before_remaining_providers_complete():
    host = controller_host()
    host.run_search(True)
    request = host.requests[-1]
    request.work()
    on_batch = host.online.call_args.args[2]
    on_batch(NS(provider_id="public-A", result_status="unavailable",
                error_code="SearchTemporarilyUnavailable", results=[]))
    host._populate_search.assert_not_called()  # Worker thread cannot paint.
    assert host._online_source_statuses == {}
    callback, args = host.scheduled.pop()
    assert callback(*args) is False
    assert host._online_source_statuses == {"public-A": "unavailable"}
    host._populate_search.assert_called_once_with([], announce=False)
    # The other source is still pending; its first result should keep A's
    # closed verdict and render without waiting for the final summary.
    on_batch(NS(provider_id="public-B", result_status="live",
                error_code="", results=[NS(id="book", source_provider="public-B")]))
    callback, args = host.scheduled.pop()
    assert callback(*args) is False
    assert host._online_source_statuses == {
        "public-A": "unavailable", "public-B": "live",
    }
    assert len(host._remote_search_results) == 1
    assert host._populate_search.call_count == 2
    assert not host.toast.called and not host.show_error.called


def test_empty_provider_status_does_not_repaint_from_old_query_or_identity():
    host = controller_host()
    old_book = NS(id="private-prior", source_provider="public")
    host._remote_query_key = ("previous", "all", "all")
    host._remote_search_results = [old_book]
    host.run_search(True)
    assert host._remote_search_results == []
    host.requests[-1].work()
    on_batch = host.online.call_args.args[2]
    on_batch(NS(provider_id="public", result_status="unavailable",
                error_code="SearchTemporarilyUnavailable", results=[]))
    host.service.current_identity = NS(identity_id="another")
    callback, args = host.scheduled.pop()
    assert callback(*args) is False
    assert host._online_source_statuses == {}
    host._populate_search.assert_not_called()
    # Malformed provider status does not trigger an empty redraw or echo input.
    host.service.current_identity = NS(identity_id="alice")
    on_batch(NS(provider_id="public", result_status="https://private.invalid/?token=SECRET",
                error_code="SearchTemporarilyUnavailable", results=[]))
    callback, args = host.scheduled.pop()
    assert callback(*args) is False
    assert host._online_source_statuses == {}
    host._populate_search.assert_not_called()
