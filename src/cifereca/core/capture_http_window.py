"""#4093: isolate the process-wide HTTP Trace buffer to one GUI capture window.

HTTP_TRACE has a per-process RAM ring shared with Diagnostics. A second
--capture invocation in the SAME interpreter must never include the previous
session's cookies, URLs, HTML or bodies in its newly created ZIP. This
read-only projection uses an exact insertion cursor, not clock filtering.

Does not clear HTTP_TRACE, alter its budget or make any network request.
"""
from __future__ import annotations


class CaptureHTTPWindowError(ValueError):
    """A closed reason code; never includes source or private payload."""


def capture_http_window(
    snapshot: dict[str, object], initial_status: dict[str, object],
) -> dict[str, object]:
    """Project only exchanges appended after this capture's start cursor.

    The HTTPTraceCapture list is append-only. If its identity or accounting
    changed unexpectedly, reject rather than publish misattributed traffic.
    A nonzero initial cursor remains intentionally invisible to the ZIP.
    """
    if (type(snapshot) is not dict
            or snapshot.get("schema") != "cifereca.http-trace/1"
            or type(initial_status) is not dict
            or type(snapshot.get("session_ref")) is not str
            or snapshot["session_ref"] != initial_status.get("session_ref")
            or type(snapshot.get("exchanges")) is not list):
        raise CaptureHTTPWindowError("capture_http_window_unavailable")
    for field, current_key in (
        ("exchanges", None),
        ("omitted_exchanges", "omitted_exchanges"),
        ("used_bytes", "used_bytes"),
    ):
        initial = initial_status.get(field)
        current = (
            len(snapshot["exchanges"]) if current_key is None
            else snapshot.get(current_key)
        )
        if (type(initial) is not int or type(current) is not int
                or initial < 0 or current < initial):
            raise CaptureHTTPWindowError("capture_http_window_unavailable")

    # No shared object is mutated. All values originate from the already
    # bounded HTTP_TRACE.snapshot() deep copy.
    return {
        "schema": "cifereca.http-trace/1",
        "session_ref": snapshot["session_ref"],
        "representation": snapshot.get("representation"),
        "limits": snapshot.get("limits"),
        "used_bytes": snapshot["used_bytes"] - initial_status["used_bytes"],
        "omitted_exchanges": (
            snapshot["omitted_exchanges"] - initial_status["omitted_exchanges"]
        ),
        "exchanges": snapshot["exchanges"][initial_status["exchanges"]:],
    }
