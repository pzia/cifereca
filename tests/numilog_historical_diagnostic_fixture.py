"""Synthetic, privacy-safe *read-only* historical Numilog status fixture.

The retired v1 startup GET tests are gone; SQLite receipt displays still
need deterministic provenance to test non-adoption and redaction.
"""


def valid():
    return dict(
        phase="completed", cause_phase="manifest_fetch", code="manifest_failed",
        status="failed", task_kind="source-materialize", attempted=0,
        downloaded=0, skipped=0, failed=1, task_ref="abcdef012345",
        run_ref="012345abcdef",
    )
