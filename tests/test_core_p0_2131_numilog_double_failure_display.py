"""P0 #2131: a receipt-finalization failure can follow a zero-attempt failure.

Only typed, bounded persisted Core status is formatted; no GTK, provider,
storage mutation, Adobe action, downloading or live identity involved.
"""
from __future__ import annotations

import pytest

from cifereca.gui.numilog_materialization_status import describe_run
from tests.test_numilog_startup_diagnostics import valid


def _status(*, failed=2, attempted=0, downloaded=0):
    detail = {
        **valid(), "failed": failed, "attempted": attempted,
        "downloaded": downloaded,
    }
    return {
        "status": "running", "started_at": 2_000_000_000,
        "next_eligible_at": 2_000_003_600,
        "attempted": attempted, "downloaded": downloaded,
        "skipped": 0, "failed": failed,
        "diagnostic": detail,
    }


def test_manifest_and_receipt_failure_both_visible_with_zero_attempt():
    text = describe_run("on_start", _status(), now=2_000_000_000)
    assert "Dernier essai en échec" in text
    assert "0 téléchargé(s), 0 tenté(s), 0 ignoré(s), 2 échec(s)" in text
    assert "Phase : manifest_fetch ; cause : manifest_failed" in text
    assert "tâche : abcdef012345" in text
    assert "Dernier bilan indisponible" not in text


def test_nine_failures_is_the_upper_public_schema_bound_not_eight():
    # Preserve the same bounds used by startup_diagnostics.public_fields().
    text = describe_run(
        "on_start", _status(attempted=8, failed=9), now=2_000_000_000,
    )
    assert "8 tenté(s)" in text
    assert "9 échec(s)" in text


@pytest.mark.parametrize(("attempted", "failed"), [
    (0, 3), (1, 4), (8, 10), (False, 2),
])
def test_excessive_or_untyped_error_counts_still_fail_closed(attempted, failed):
    assert describe_run("on_start", _status(
        attempted=attempted, failed=failed,
    ), now=2_000_000_000) == "Dernier bilan indisponible."


def test_private_extraneous_diagnostic_field_cannot_enter_render():
    report = _status()
    report["diagnostic"]["private_url"] = "https://secret.invalid/?token=PRIVATE"
    text = describe_run("on_start", report, now=2_000_000_000)
    assert "PRIVATE" not in text
    assert "token" not in text
    assert "Phase :" not in text
    assert "0 tenté(s)" in text
