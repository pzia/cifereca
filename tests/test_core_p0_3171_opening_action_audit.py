"""#3171: a partial opening sync must not become a successful audit action.

The source may have partially converged, but the binary event outcome cannot
claim success; the precise state stays available in structured facts.
All scenarios are offline.
"""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from cifereca.core.sync_coordination import opening_sync


@pytest.mark.parametrize(
    ("sync_status", "event_outcome"),
    [
        ("success", "success"),
        ("partial", "failed"),
        ("failed", "failed"),
        ("unclassified", "failed"),
    ],
)
def test_opening_sync_audit_marks_only_complete_success_as_success(
    sync_status, event_outcome,
):
    actions = []
    identity_summary = {
        "identity_id": "profile-a",
        "status": sync_status,
        "errors": 1 if sync_status != "success" else 0,
    }
    service = SimpleNamespace(
        _opening_sync_locked=lambda **_kwargs: {"identities": [identity_summary]},
        _record_action=lambda identity_id, action, outcome, **kwargs: actions.append(
            (identity_id, action, outcome, kwargs)
        ),
    )

    result = opening_sync(
        service,
        lock_factory=lambda _path: nullcontext(),
        lock_path=lambda: "synthetic-lock",
    )

    assert result["identities"] == [identity_summary]
    assert actions == [
        (
            "profile-a",
            "synchronization",
            event_outcome,
            {
                "facts": {
                    "deep": False,
                    "status": sync_status,
                    "errors": identity_summary["errors"],
                },
            },
        )
    ]
