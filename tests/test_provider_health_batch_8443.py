"""Deterministic cross-lot foundations: no supplier I/O."""
from types import SimpleNamespace
import pytest
import httpx

from cifereca.core.provider_health_projection import PassiveHealth, Scope, Observation, Outcome
from cifereca.gui.provider_health_notifications import notify_health_transitions
from cifereca.core.provider_health_replay import CapturedReadTransport, ReplayUnavailable
from cifereca import http_trace
from cifereca.diagnostic_task_context import diagnostic_task_scope, diagnostic_task_reference


def test_grouped_notifications_outside_plugins_and_isolation():
    health = PassiveHealth()
    health.set_enabled(True)
    messages = []
    service = SimpleNamespace(_provider_health=health, current_identity=SimpleNamespace(identity_id="a"))
    window = SimpleNamespace(service=service, toast=messages.append)
    for n in range(5):
        scope = Scope("p" + str(n), "i", "a", "a", "search")
        health.observe(scope, Observation(Outcome.NETWORK, "1", "now", str(n)), lease=health.lease())
    other = Scope("private-other", "i", "b", "b", "search")
    health.observe(other, Observation(Outcome.AUTH, "1", "now", "other"), lease=health.lease())
    notify_health_transitions(window)
    assert len(messages) == 1 and "2 autre" in messages[0] and "private-other" not in messages[0]
    notify_health_transitions(window)
    assert len(messages) == 1
    window._closed = True
    service.current_identity = SimpleNamespace(identity_id="b")
    notify_health_transitions(window)
    assert len(messages) == 1
    window._closed = False
    notify_health_transitions(window)
    assert "private-other" in messages[1]


def test_witnesses_bounded_exact_scope_and_no_replacement_on_absence():
    health = PassiveHealth()
    health.set_enabled(True)
    a = Scope("p", "i", "a", "a", "search")
    b = Scope("p", "j", "b", "b", "search")
    first = Observation(Outcome.SUCCESS, "1", "now", "a")
    health.observe(a, first, lease=health.lease())
    health.retain_book_witnesses(a, first, (("db", "1"), ("db", "2"), ("db", "3"), ("db", "4")),
                                lease=health.lease())
    assert len(health.book_witnesses(a)) == 3
    assert health.book_witnesses(b) == ()
    later = Observation(Outcome.ATTESTED_EMPTY, "1", "later", "later")
    health.observe(a, later, lease=health.lease())
    health.retain_book_witnesses(a, later, (("db", "new"),), lease=health.lease())
    assert [row[1][1] for row in health.book_witnesses(a)] == ["1", "2", "3"]
    health.set_enabled(False)
    assert health.book_witnesses(a) == ()


def test_replay_exact_tape_no_fallback(monkeypatch):
    monkeypatch.setattr(http_trace, "_trace_active", lambda: True)
    capture = http_trace.HTTPTraceCapture()
    with diagnostic_task_scope():
        ref = diagnostic_task_reference()
        request = httpx.Request("GET", "https://fixture.invalid/read")
        exchange = capture.begin(request, "fixture")
        exchange.finish_body("request", "complete")
        exchange.response(httpx.Response(200, headers={"content-type": "text/plain"}))
        exchange.observe("response", b"fixture")
        exchange.finish_body("response", "complete")
        exchange.terminal("complete")
    snapshot = capture.snapshot()
    snapshot["session_ref"] = ref
    payload = http_trace.build_http_trace_zip(snapshot)
    transport = CapturedReadTransport(payload, evidence_ref=ref, producer="fixture")
    with pytest.raises(ReplayUnavailable, match="replay_unmatched_request"):
        transport.handle_request(httpx.Request("GET", "https://fixture.invalid/other"))
    with pytest.raises(ReplayUnavailable, match="replay_incomplete"):
        transport.require_complete()
    good = CapturedReadTransport(payload, evidence_ref=ref, producer="fixture")
    assert good.handle_request(request).read() == b"fixture"
    good.require_complete()
    with pytest.raises(ReplayUnavailable, match="replay_unmatched_request"):
        good.handle_request(request)
    with pytest.raises(ReplayUnavailable):
        CapturedReadTransport(payload, evidence_ref=ref, producer="other")
