"""#1599: third-party exception class names are not diagnostic-safe data.

Synthetic exceptions only; no HTTP, GTK, accounts or files.
"""
import json

import pytest

from youth_catalog import YouthCatalogIndex, collection_failure_diagnostic


@pytest.mark.parametrize("name", (
    "Bearer_PRIVATE_ACCOUNT_TOKEN",
    "Path_home_PRIVATE_USER_books",
    "ReadTimeout_PRIVATE_HOST",
    "Cancelled_PRIVATE_CREDENTIAL",
))
def test_dynamic_private_exception_class_names_never_leave_failure_projection(name):
    Untrusted = type(name, (RuntimeError,), {})
    failure = Untrusted("https://provider.example/?token=PRIVATE_VALUE")
    failure.http_status = 503
    diagnostic = collection_failure_diagnostic(failure)
    assert diagnostic == {
        "state": "http_error", "error_type": "SourceError",
        "http_status": 503, "request_count": None,
    }
    assert "PRIVATE" not in json.dumps(diagnostic)


def test_normal_timeouts_and_cancelled_exceptions_keep_safe_cause():
    timeout = collection_failure_diagnostic(TimeoutError("PRIVATE_LOCATION"))
    assert timeout == {
        "state": "timeout", "error_type": "TimeoutError",
        "http_status": None, "request_count": None,
    }
    from youth_page import YouthSourceCancelled
    cancelled = collection_failure_diagnostic(YouthSourceCancelled())
    assert cancelled["state"] == "cancelled"
    assert cancelled["error_type"] == "YouthSourceCancelled"


def test_direct_recording_rejects_identifier_shaped_private_error_type():
    index = YouthCatalogIndex()
    index.record_collection(
        "synthetic-identity", "milan", state="error",
        error_type="Bearer_PRIVATE_ACCOUNT_TOKEN",
    )
    row = next(r for r in index.source_diagnostics("synthetic-identity") if r["source_id"] == "milan")
    assert row["error_type"] == ""
    assert "PRIVATE" not in json.dumps(row)
    index.record_collection("synthetic-identity", "milan", state="timeout", error_type="ReadTimeout")
    row = next(r for r in index.source_diagnostics("synthetic-identity") if r["source_id"] == "milan")
    assert row["error_type"] == "ReadTimeout"


def test_safe_generic_error_can_be_stored_without_private_class():
    index = YouthCatalogIndex()
    index.record_collection(
        "synthetic-identity", "ecole-loisirs", state="error",
        **collection_failure_diagnostic(type("Account_PRIVATE_SECRET", (OSError,), {})("hidden")),
    )
    row = next(r for r in index.source_diagnostics("synthetic-identity") if r["source_id"] == "ecole-loisirs")
    assert row["collection_state"] == "error"
    assert row["error_type"] == "SourceError"
    assert "PRIVATE" not in json.dumps(row)
