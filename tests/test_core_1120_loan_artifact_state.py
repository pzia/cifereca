"""P0 #1120: characterize read-only inspection of a previous loan artifact."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.core.loan_artifact_state import inspect_previous_loan_artifact


def _row(path: Path, *, status="complete", executor="provider", processed=True):
    return {
        "identity_id": "alex", "credential_id": "paris-a", "account_id": "primary",
        "item_id": "book-a", "strategy": "loan_auto", "status": status,
        "executor": executor, "metadata": {"path": str(path), "post_processed": processed},
    }


def _inspect(rows, *, inspector, account_id="primary", source_provider="paris"):
    return inspect_previous_loan_artifact(
        rows, identity_id="alex", credential_id="paris-a",
        account_id=account_id, item_id="book-a", source_provider=source_provider,
        artifact_inspector=inspector,
    )


def test_scope_is_exact_and_rows_are_unchanged(tmp_path):
    local = tmp_path / "book.epub"
    local.write_bytes(b"fake; inspector is deliberately injected")
    original = _row(local)
    rows = [
        {**original, "identity_id": "other"},
        {**original, "credential_id": "paris-b"},
        {**original, "account_id": "linked"},
        {**original, "item_id": "book-b"},
        {**original, "strategy": "manual"},
        original,
    ]
    snapshot = deepcopy(rows)
    calls = []

    def inspector(path, expected):
        calls.append((path, expected))
        return SimpleNamespace(valid=True, protected=False)

    state = _inspect(rows, inspector=inspector)
    assert state.row is original
    assert state.path == local
    assert state.complete_local is True
    assert state.legacy_acsm_artifact is False
    assert calls == [(local, "epub")]
    assert rows == snapshot
    assert local.read_bytes() == b"fake; inspector is deliberately injected"


@pytest.mark.parametrize(
    ("status", "executor", "processed", "valid", "protected", "legacy", "complete"),
    [
        ("complete", "provider", False, True, False, False, True),
        ("complete", "kobo-adept", True, True, False, False, True),
        ("complete", "kobo-adept", False, True, False, True, False),
        ("attention_required", "kobo-adept", True, True, False, False, False),
        ("complete", "provider", True, True, True, False, False),
        ("complete", "kobo-adept", True, False, False, True, False),
    ],
)
def test_legacy_and_reuse_decisions_are_unchanged(
    tmp_path, status, executor, processed, valid, protected, legacy, complete,
):
    path = tmp_path / "book.pdf"
    path.write_bytes(b"fake")
    seen = []

    def inspector(file, expected):
        seen.append((file, expected))
        return SimpleNamespace(valid=valid, protected=protected)

    result = _inspect([_row(path, status=status, executor=executor, processed=processed)], inspector=inspector)
    assert result.legacy_acsm_artifact is legacy
    assert result.complete_local is complete
    assert seen == [(path, "pdf")]


def test_absent_or_missing_artifact_never_triggers_inspector(tmp_path):
    def unexpected(*_args):
        pytest.fail("an absent file must not be inspected")

    missing = tmp_path / "missing.epub"
    result = _inspect([_row(missing, executor="kobo-adept", processed=False)], inspector=unexpected)
    assert result.row is not None
    assert result.path == missing
    assert result.inspection is None
    assert not result.complete_local
    assert not result.legacy_acsm_artifact
    empty = _inspect([], inspector=unexpected)
    assert empty.row is None
    assert empty.metadata == {}
    assert empty.path is None
    assert not empty.complete_local


def test_provider_scope_does_not_mark_other_provider_as_paris_legacy(tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(b"fake")
    result = _inspect(
        [_row(path, executor="kobo-adept", processed=False)],
        source_provider="kobo",
        inspector=lambda *_: SimpleNamespace(valid=False, protected=True),
    )
    assert result.legacy_acsm_artifact is False
    assert result.complete_local is False
