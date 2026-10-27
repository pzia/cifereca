"""#2797: selected-pair declarations have a separate explicit CoreUI bridge."""
import json

import pytest

from cifereca.core.library_audit import audit_library
from cifereca.core.library_inspection_cache import _make_session_stamp_guard, _service_stamp
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_library_declared_links import rig, records, rows


@pytest.fixture
def selected(rig):
    service, _, root, _, _ = rig
    audit = audit_library(service.storage.path, {'default': (root,)})
    assert audit.redacted()['status'] == 'complete', audit.redacted()['reasons']
    session = LocalInspection(audit, lambda: True, 'default', roots=(root,), service=service)
    right, = session.page(state='rights_without_verified_file').cases
    file, = session.unconfirmed_files(limit=10).cases
    try:
        yield session, right.key, file.key, service, rig
    finally:
        session.close()


def preview(selected, **options):
    session, right, file, _, _ = selected
    return session.preview_declared_link(right, file, **options)


def confirm(selected, **options):
    session, right, file, _, _ = selected
    return session.confirm_declared_link(right, file, **options)


def test_declaration_choice_is_explicit_distinct_and_token_never_leaves_core(selected):
    session, right, file, service, rig = selected
    context = session.declaration_context(right, file)
    assert context['provider'] == rig[4]['provider_id']
    assert context['account'] == rig[4]['account_id']
    assert context['edition'] == rig[4]['item_id']
    # Strict attested path continues to refuse these historical purchases
    # without receipts/backups; no automatic substitution of trust modes.
    assert session.preview_manual_attachment(right, file)['status'] == 'unavailable'
    assert confirm(selected, user_confirmed=True)['status'] == 'unavailable'
    result = preview(selected)
    assert result['status'] == 'preview'
    assert result['trust'] == 'user_declaration'
    assert result['change'] == 'record_declaration'
    assert result['requires_explicit_confirmation'] is True
    assert 'PRIVATE' not in json.dumps(result)
    assert 'plan_token' not in result and 'relation_id' not in result
    assert records(service) == []
    assert confirm(selected, user_confirmed=False)['status'] == 'unavailable'
    assert records(service) == []
    preview(selected)
    applied = confirm(selected, user_confirmed=True)
    assert applied['status'] == 'declared'
    assert applied['revoke_available'] is True
    assert applied['rights_mutations'] == applied['placement_mutations'] == 0
    assert confirm(selected, user_confirmed=True)['status'] == 'unavailable'
    assert len(records(service)) == 1 and not rows(service, 'placements')
    assert session._manual_undo is None


def test_revocation_is_separately_previewed_and_confirmed(selected):
    service = selected[3]
    preview(selected)
    confirm(selected, user_confirmed=True)
    assert preview(selected, revoke=True)['status'] == 'revoke_preview'
    assert records(service)[0]['state'] == 'active'
    assert confirm(selected, user_confirmed=False)['status'] == 'unavailable'
    assert records(service)[0]['state'] == 'active'
    assert preview(selected, revoke=True)['change'] == 'revoke_declaration'
    result = confirm(selected, user_confirmed=True)
    assert result['status'] == 'revoked' and result['revoke_available'] is False
    assert records(service)[0]['state'] == 'revoked'
    assert not rows(service, 'placements')


def test_selection_change_and_attested_action_revoke_declared_confirmation(selected):
    session, right, file, service, _ = selected
    preview(selected)
    session.revoke_manual_attachment_preview()
    assert confirm(selected, user_confirmed=True)['status'] == 'unavailable'
    preview(selected)
    session.preview_manual_attachment(right, file)
    assert confirm(selected, user_confirmed=True)['status'] == 'unavailable'
    assert records(service) == []


def test_closed_or_foreign_case_cannot_declare(selected):
    session, right, file, service, _ = selected
    assert session.preview_declared_link(file, right)['status'] == 'unavailable'
    assert session.preview_declared_link('right:9999', file)['status'] == 'unavailable'
    preview(selected)
    assert session.confirm_declared_link(right, 'file:9999', user_confirmed=True)[
        'status'] == 'unavailable'
    preview(selected)
    session.close()
    assert confirm(selected, user_confirmed=True)['status'] == 'unavailable'
    assert session._declared_right_scopes == {}
    assert session._declared_pending is None and session._declared_last is None
    assert records(service) == []


def test_changed_file_and_cancelled_worker_cannot_consume_old_selection(selected):
    session, _, _, service, rig = selected
    assert preview(selected, cancelled=lambda: True)['status'] == 'blocked'
    assert records(service) == []
    preview(selected)
    rig[1].write_bytes(b'changed file')
    assert confirm(selected, user_confirmed=True)['status'] == 'blocked'
    assert records(service) == []


def test_inflight_preview_revocation_destroys_new_token(selected, monkeypatch):
    from cifereca.core import library_declared_links as engine
    session, _, _, service, _ = selected
    original = engine.declare_local_link

    def revoked(*args, **kwargs):
        result = original(*args, **kwargs)
        session.revoke_manual_attachment_preview()
        return result

    monkeypatch.setattr(engine, 'declare_local_link', revoked)
    assert preview(selected)['status'] == 'unavailable'
    assert session._declared_pending is None and records(service) == []


def test_cache_accepts_only_actual_commit_and_foreign_sql_still_revokes(selected):
    from time import monotonic
    session, _, _, service, _ = selected
    session._valid, session._accept_own_core_commit = _make_session_stamp_guard(
        service, lambda: True, monotonic() + 300, _service_stamp(service))
    assert preview(selected)['status'] == 'preview'
    assert confirm(selected, user_confirmed=True)['revoke_available'] is True
    assert session.valid()
    assert preview(selected, revoke=True)['status'] == 'revoke_preview'
    # An unrelated writer after the declaration cannot be acknowledged as ours.
    service.storage.metadata_set('foreign-change', '1')
    assert not session.valid()
    assert confirm(selected, user_confirmed=True)['status'] == 'unavailable'
    assert records(service)[0]['state'] == 'active'


def test_reopened_inspection_can_discover_and_revoke_existing_declaration(selected):
    session, _, _, service, rig = selected
    preview(selected)
    confirm(selected, user_confirmed=True)
    session.close()
    root = rig[2]
    audit = audit_library(service.storage.path, {'default': (root,)})
    second = LocalInspection(audit, lambda: True, 'default', roots=(root,), service=service)
    try:
        right, = second.page(state='rights_without_verified_file').cases
        file, = second.unconfirmed_files(limit=10).cases
        view = second.preview_declared_link(right.key, file.key)
        assert view['status'] == 'already_declared' and view['revoke_available']
        assert view['presence_state'] == 'present_declared'
        assert view['trust'] == 'user_declaration' and view['read_only'] is True
        assert 'relation_id' not in view
        assert second.preview_declared_link(right.key, file.key, revoke=True)[
            'status'] == 'revoke_preview'
        assert second.confirm_declared_link(right.key, file.key, user_confirmed=True)[
            'status'] == 'revoked'
    finally:
        second.close()


def test_changed_loan_is_explained_instead_of_silently_redeclared(selected):
    session, _, _, service, rig = selected
    preview(selected)
    confirm(selected, user_confirmed=True)
    before = records(service)
    service.storage.entitlement_put({**rig[3], 'state': 'expired'})
    changed = preview(selected)
    assert changed['status'] == 'needs_review'
    assert changed['presence_state'] == 'declared_needs_review'
    assert changed['revoke_available'] is True
    assert records(service) == before
    assert preview(selected, revoke=True)['status'] == 'revoke_preview'
    assert confirm(selected, user_confirmed=True)['status'] == 'revoked'


def test_replaced_file_can_revoke_old_statement_in_a_new_inspection(selected):
    from tests.test_library_reconciliation import epub
    session, _, _, service, rig = selected
    preview(selected)
    confirm(selected, user_confirmed=True)
    before = records(service)
    session.close()
    epub(rig[1], title='replacement')
    root = rig[2]
    audit = audit_library(service.storage.path, {'default': (root,)})
    second = LocalInspection(audit, lambda: True, 'default', roots=(root,), service=service)
    try:
        right, = second.page(state='rights_without_verified_file').cases
        file, = second.unconfirmed_files(limit=10).cases
        view = second.preview_declared_link(right.key, file.key)
        assert view['status'] == 'needs_review' and view['revoke_available']
        assert view['presence_state'] == 'declared_needs_review'
        assert records(service) == before
        second.preview_declared_link(right.key, file.key, revoke=True)
        assert second.confirm_declared_link(right.key, file.key, user_confirmed=True)[
            'status'] == 'revoked'
        assert rig[1].read_bytes() != b''
    finally:
        second.close()


@pytest.mark.parametrize('rig', ['numilog'], indirect=True)
def test_numilog_attention_required_receipt_does_not_block_private_declaration(selected):
    """Historical incomplete acquisition must not require invented receipts."""
    session, right_key, file_key, service, fixture = selected
    _service, _book, _root, right, _options = fixture
    service.storage.acquisition_put({
        'acquisition_id': 'synthetic-incomplete-acquisition',
        'entitlement_id': right['entitlement_id'],
        'provider_id': 'numilog',
        'credential_id': right['credential_id'],
        'account_id': right['account_id'],
        'identity_id': right['identity_id'],
        'item_id': right['item_id'],
        'status': 'attention_required',
        'attention_code': 'artifact_not_published',
    })
    ledger_before = rows(service, 'acquisition_ledger')
    # The strict provider-attested route must still refuse an incomplete
    # receipt; a user statement is a separate, lower-trust operation.
    assert session.preview_manual_attachment(right_key, file_key)['status'] == 'unavailable'
    assert session.preview_declared_link(right_key, file_key)['status'] == 'preview'
    answer = session.confirm_declared_link(right_key, file_key, user_confirmed=True)
    assert answer['status'] == 'declared'
    assert answer['trust'] == 'user_declaration'
    assert rows(service, 'acquisition_ledger') == ledger_before
    assert not rows(service, 'placements')
    assert len(records(service)) == 1
    for _ in range(2):
        service.storage.entitlement_put(right)
        assert len(records(service)) == 1
        assert rows(service, 'acquisition_ledger') == ledger_before


def test_private_history_projection_is_scoped_and_never_attested(selected):
    session, right, file, service, fixture = selected
    initial = session.declared_links_page()
    assert initial is not None and initial.rows == () and initial.total == 0
    confirmed = session.overview()['confirmed_files']
    assert preview(selected)['status'] == 'preview'
    assert confirm(selected, user_confirmed=True)['status'] == 'declared'
    for _ in range(2):
        service.storage.entitlement_put(fixture[3])
        listing = session.declared_links_page()
        assert listing is not None and listing.total == 1
        assert listing.rows[0].review == 'recorded_unverified_file'
        assert listing.rows[0].provider_id == fixture[4]['provider_id']
        assert listing.rows[0].file_path == str(fixture[1])
        assert session.overview()['confirmed_files'] == confirmed
    service.storage.entitlement_put({**fixture[3], 'state': 'expired'})
    changed = session.declared_links_page()
    assert changed.total == 1
    assert changed.rows[0].review == 'source_or_right_changed'
    assert session.declared_links_page(limit=0) is None
    assert not rows(service, 'placements')
    session.close()
    assert session.declared_links_page() is None


def test_obsolete_declaration_is_withdrawable_after_file_and_account_disappear(selected):
    session, right, file, service, fixture = selected
    assert preview(selected)['status'] == 'preview'
    assert confirm(selected, user_confirmed=True)['status'] == 'declared'
    relation = records(service)[0]['relation_id']
    fixture[1].unlink()
    with service.storage._lock, service.storage._connect() as db:
        db.execute('DELETE FROM entitlements')
    service.accounts = []
    before = {table: rows(service, table) for table in (
        'entitlements', 'acquisition_ledger', 'artifacts', 'placements',
        'provider_connections',
    )}
    plan = session.preview_history_withdrawal(relation)
    assert plan['status'] == 'revoke_preview'
    assert plan['trust'] == 'user_declaration'
    assert plan['requires_explicit_confirmation'] is True
    assert plan['change'] == 'revoke_declaration'
    assert 'plan_token' not in plan and 'PRIVATE' not in json.dumps(plan)
    assert session.confirm_history_withdrawal(
        relation, user_confirmed=False)['status'] == 'unavailable'
    assert records(service)[0]['state'] == 'active'
    session.preview_history_withdrawal(relation)
    answer = session.confirm_history_withdrawal(relation, user_confirmed=True)
    assert answer['status'] == 'revoked'
    assert answer['rights_mutations'] == answer['placement_mutations'] == 0
    assert records(service)[0]['state'] == 'revoked'
    assert {table: rows(service, table) for table in before} == before


def test_history_withdrawal_selection_change_invalidates_token(selected):
    session, right, file, service, fixture = selected
    preview(selected)
    confirm(selected, user_confirmed=True)
    relation = records(service)[0]['relation_id']
    session.preview_history_withdrawal(relation)
    session.revoke_manual_attachment_preview()
    assert session.confirm_history_withdrawal(
        relation, user_confirmed=True)['status'] == 'unavailable'
    assert records(service)[0]['state'] == 'active'
    assert session.preview_history_withdrawal('0' * 32)['status'] == 'blocked'
    session.close()
    assert session.preview_history_withdrawal(relation)['status'] == 'unavailable'


@pytest.mark.parametrize('rig', ['kobo', 'numilog', 'paris'], indirect=True)
def test_fresh_declared_pair_is_present_without_a_supplier_receipt(selected):
    session, right, file, service, rig_data = selected
    # This remains a user statement even after an explicit, fresh Core check.
    assert preview(selected)['status'] == 'preview'
    assert confirm(selected, user_confirmed=True)['status'] == 'declared'
    before = {table: rows(service, table) for table in (
        'entitlements', 'acquisition_ledger', 'backup_items', 'artifacts',
        'placements', 'catalog_entries',
    )}
    saved = records(service)
    initial_files = session.overview()['confirmed_files']
    observed = preview(selected)
    assert observed == {
        'status': 'already_declared', 'presence_state': 'present_declared',
        'read_only': True, 'trust': 'user_declaration',
        'revoke_available': True,
    }
    assert 'PRIVATE' not in json.dumps(observed)
    assert session.overview()['confirmed_files'] == initial_files
    assert {table: rows(service, table) for table in before} == before
    assert records(service) == saved
    assert rig_data[1].is_file()
    assert session.preview_declared_link(right, file, revoke=True)['status'] == 'revoke_preview'


@pytest.mark.parametrize('rig', ['kobo', 'numilog'], indirect=True)
def test_declared_local_presence_downgrades_when_account_is_switched(selected):
    session, _, _, service, _ = selected
    assert preview(selected)['status'] == 'preview'
    assert confirm(selected, user_confirmed=True)['status'] == 'declared'
    assert preview(selected)['presence_state'] == 'present_declared'
    before = records(service)
    service.accounts[0].unique_id = 'new-synthetic-account'
    outcome = preview(selected)
    assert outcome['status'] == 'needs_review'
    assert outcome['presence_state'] == 'declared_needs_review'
    assert outcome['revoke_available'] is True
    assert records(service) == before
    assert rows(service, 'placements') == []
