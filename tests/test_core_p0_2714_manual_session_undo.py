"""#2714: private same-pair undo tokens never escape or authorize stale actions."""
from __future__ import annotations

import json

import pytest

from tests.test_core_p0_2714_manual_session_bridge import selected
from tests.test_core_p0_2714_numilog_manual_transaction import _rows


def _attach(selected):
    session, right, file, service = selected
    assert session.preview_manual_attachment(right, file)['status'] == 'preview'
    confirmed = session.confirm_manual_attachment(
        right, file, user_confirmed=True,
    )
    assert confirmed['status'] == 'applied'
    assert confirmed['undo_available'] is True
    assert 'operation_id' not in confirmed
    assert len(_rows(service, 'placements')) == 1
    return session, right, file, service


def test_private_undo_requires_separate_preview_and_consent(selected):
    session, right, file, service = _attach(selected)
    assert session.confirm_manual_undo(
        right, file, user_confirmed=True,
    )['status'] == 'unavailable'
    assert len(_rows(service, 'placements')) == 1
    preview = session.preview_manual_undo(right, file)
    assert preview == {
        'status': 'undo_preview', 'read_only': True,
        'requires_explicit_confirmation': True,
    }
    assert 'plan_token' not in json.dumps(preview)
    assert len(_rows(service, 'placements')) == 1
    result = session.confirm_manual_undo(
        right, file, user_confirmed=True,
    )
    assert result == {'status': 'undone', 'read_only': False}
    assert _rows(service, 'placements') == []
    assert session.confirm_manual_undo(
        right, file, user_confirmed=True,
    )['status'] == 'unavailable'


def test_pair_change_cancels_undo_token_but_not_the_operation(selected):
    session, right, file, service = _attach(selected)
    assert session.preview_manual_undo(right, file)['status'] == 'undo_preview'
    session.revoke_manual_attachment_preview()
    assert session.confirm_manual_undo(
        right, file, user_confirmed=True,
    )['status'] == 'unavailable'
    assert len(_rows(service, 'placements')) == 1
    assert session.preview_manual_undo(right, file)['status'] == 'undo_preview'
    assert session.confirm_manual_undo(
        right, file, user_confirmed=False,
    )['status'] == 'unavailable'
    assert len(_rows(service, 'placements')) == 1


def test_wrong_pair_and_close_refuse_undo_after_confirmed_attachment(selected):
    session, right, file, service = _attach(selected)
    assert session.preview_manual_undo(file, right)['status'] == 'unavailable'
    assert session.preview_manual_undo(right, 'file:9999')['status'] == 'unavailable'
    assert session.preview_manual_undo(right, file)['status'] == 'undo_preview'
    session.close()
    assert session.confirm_manual_undo(
        right, file, user_confirmed=True,
    )['status'] == 'unavailable'
    assert session._manual_undo is None
    assert session._manual_undo_pending is None
    assert len(_rows(service, 'placements')) == 1


def test_undo_rejected_when_sql_evidence_changes_but_placement_remains(selected):
    session, right, file, service = _attach(selected)
    assert session.preview_manual_undo(right, file)['status'] == 'undo_preview'
    with service.storage._lock, service.storage._connect() as db:
        db.execute('UPDATE acquisition_ledger SET updated_at=updated_at+1')
    result = session.confirm_manual_undo(
        right, file, user_confirmed=True,
    )
    assert result['status'] == 'blocked'
    assert len(_rows(service, 'placements')) == 1
    assert 'PRIVATE' not in json.dumps(result)
