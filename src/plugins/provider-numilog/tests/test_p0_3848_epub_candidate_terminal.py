"""#3848: an EPUB candidate is a successful *staging* event, not ADEPT failure.

These tests are offline and never authorize publication, rights or delivery.
"""
from __future__ import annotations

import pytest

from cifereca_numilog_delivery_trace import DeliveryTrace


class RecordingDiagnostics:
    def __init__(self):
        self.events = []

    def add(self, category, operation, status, *, detail, level, duration_ms):
        self.events.append((category, operation, status, detail, level, duration_ms))


@pytest.mark.parametrize("code", [
    "epub_candidate", "acsm_candidate", "epub_complete", "acsm_complete",
])
def test_known_terminal_success_codes_are_reported_as_ok(code):
    diagnostics = RecordingDiagnostics()
    with DeliveryTrace(diagnostics, source="owned_card", connected_initial=True) as trace:
        trace.event("artifact_validation", code, hops=2, mime="application/epub+zip")
        trace.success(code)

    terminal = [event for event in diagnostics.events if event[4] == "normal"]
    assert len(terminal) == 1
    category, operation, status, detail, level, duration = terminal[0]
    assert (category, operation, status) == ("NUMILOG", "acquisition_delivery", "ok")
    assert detail["phase"] == "completed"
    assert detail["code"] == code
    assert "adept_unconfirmed" not in repr(terminal)
    assert duration is not None
    assert any(e[3]["code"] == code and e[3]["phase"] == "artifact_validation"
               for e in diagnostics.events if e[4] == "detailed")


def test_candidate_is_not_publication_and_unrecognized_success_fails_closed():
    diagnostics = RecordingDiagnostics()
    with DeliveryTrace(diagnostics, source="owned_card") as trace:
        trace.fail("artifact_invalid")
        trace.success("arbitrary_plugin_result")
    terminal = diagnostics.events[-1]
    assert terminal[2] == "error"
    assert terminal[3]["code"] == "artifact_invalid"
    assert not any(e[2] == "ok" and e[3]["phase"] == "completed"
                   for e in diagnostics.events)


def test_exception_after_candidate_event_does_not_emit_false_success():
    diagnostics = RecordingDiagnostics()
    with pytest.raises(RuntimeError, match="synthetic"):
        with DeliveryTrace(diagnostics, source="owned_card") as trace:
            trace.fail("artifact_invalid")
            trace.event("artifact_validation", "epub_candidate", hops=1)
            trace.success("epub_candidate")
            raise RuntimeError("synthetic")
    terminal = diagnostics.events[-1]
    assert terminal[2] == "error"
    # This is a staging event, not a finalized artifact or Core Placement.
    assert terminal[3]["phase"] == "completed"
    assert terminal[3]["code"] == "artifact_invalid"
