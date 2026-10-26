"""Renewal consumes one scoped snapshot; failed reads never authorize a POST."""
from dataclasses import replace

import pytest

from cifereca.plugin_api import LoanInventoryScope, LoanInventorySnapshot
from cifereca.core.provider_loan_renewal import _source_key
from tests.test_core_p1_3121_paris_renewal_one_post import setup


@pytest.mark.parametrize('status', ['success', 'partial', 'failed', 'cancelled'])
def test_renewal_uses_snapshot_status_and_never_calls_legacy_reader(tmp_path, status):
    service, account, selected, session = setup(tmp_path)
    reads = []
    def snapshot(owner):
        reads.append(owner)
        # Side-channel contradictions must not change the atomic result.
        session.last_loan_errors = [] if status in {'failed', 'cancelled'} else [{'code': 'read_failed'}]
        return LoanInventorySnapshot(LoanInventoryScope.for_account(owner),
                                     tuple(session.inventory), status=status)
    session.loan_inventory_snapshot = snapshot
    session.loans = lambda _: pytest.fail('typed reader must not perform legacy GET')
    try:
        result = service.renew(selected, confirmed=True)
        assert reads == [account]
        if status in {'success', 'partial'}:
            assert result['status'] == 'submitted' and len(session.posts) == 1
            assert result['provider_verified'] is False
            assert service.renew(selected, confirmed=True)['status'] == 'submitted'
            assert reads == [account] and len(session.posts) == 1
        else:
            assert result['status'] == 'blocked' and not session.posts
            assert result['code'] == 'loan_read_unavailable'
            assert service.storage.loan_renewal_state(_source_key(selected)) is None
        assert service.storage.entitlements() == []
        assert service.storage.acquisitions() == service.storage.placements() == []
    finally:
        service.close()


@pytest.mark.parametrize('change', ['session', 'account', 'identity_aba', 'foreign_snapshot', 'mutated_row'])
def test_typed_renewal_rejects_stale_or_foreign_result_before_claim(tmp_path, change):
    service, account, selected, session = setup(tmp_path)
    def snapshot(owner):
        result = LoanInventorySnapshot(LoanInventoryScope.for_account(owner),
                                       tuple(session.inventory), status='partial')
        if change == 'session':
            service._sessions[service._session_key('paris', account.credential_id)] = object()
        elif change == 'account':
            service.accounts = [replace(account)]
        elif change == 'identity_aba':
            service.select_identity('alex')
            service.select_identity('default')
        elif change == 'foreign_snapshot':
            result = LoanInventorySnapshot(LoanInventoryScope('alex', 'paris', 'other', 'reader'), (), status='success')
        else:
            result.loans[0].source_account_id = 'foreign-reader'
        return result
    session.loan_inventory_snapshot = snapshot
    session.loans = lambda _: pytest.fail('no legacy fallback after rejected typed read')
    try:
        assert service.renew(selected, confirmed=True)['status'] == 'blocked'
        assert session.posts == []
        assert service.storage.loan_renewal_state(_source_key(selected)) is None
    finally:
        # Restore for the fixture's close protocol.
        service._sessions[service._session_key('paris', account.credential_id)] = session
        service.close()


def test_legacy_partial_adapter_still_reads_once_without_consulting_side_channel(tmp_path):
    service, account, selected, session = setup(tmp_path)
    session.last_loan_errors = [{'code': 'sibling_branch_failed'}]
    try:
        result = service.renew(selected, confirmed=True)
        assert result['status'] == 'submitted'
        assert session.reads == [account.unique_id] and len(session.posts) == 1
        assert service.renew(selected, confirmed=True)['status'] == 'submitted'
        assert len(session.reads) == len(session.posts) == 1
    finally:
        service.close()
