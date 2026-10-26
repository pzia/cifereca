"""Account-scoped opt-in BnF loan materialization during Core synchronization."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..artifact_inspection import inspect_artifact
from ..models import Account, Loan
from ..plugin_api import Cancelled
from .bnf_explicit_acsm import _valid_loan_token


def policy_key(account: Account) -> str:
    from ..brokers import SynchronizationBrokerImpl
    return 'sync.source.bnf_auto.' + SynchronizationBrokerImpl._source_id(account)


def binding_ready(service, account: Account) -> bool:
    try:
        if (type(account) is not Account or account.provider_id != 'bnf-pret'
                or not service.sync_source_enabled(account)
                or service.identity_by_id(account.identity_id) is None
                or service.storage.identity_for_connection(
                    'bnf-pret', account.credential_id) != account.identity_id):
            return False
        session = service._sessions.get(service._session_key('bnf-pret', account.credential_id))
        return (getattr(session, 'connected', None) is True
                and sum(row is account for row in getattr(session, 'accounts', ())) == 1
                and sum(row is account for row in service.accounts) == 1
                and sum(type(row) is Account and row.provider_id == 'bnf-pret'
                        and row.credential_id == account.credential_id
                        for row in service.accounts) == 1)
    except Exception:
        return False


def enabled(service, account: Account) -> bool:
    try:
        return (binding_ready(service, account)
                and service.storage.metadata_get(policy_key(account)) == '1')
    except Exception:
        return False


def materialize(service, account: Account, loan: Loan, *, control=None) -> dict:
    """Called only under the existing source/opening sync acquisition lock."""
    if not enabled(service, account) or type(loan) is not Loan:
        return {'status': 'skipped'}
    if (loan.source_provider != 'bnf-pret' or loan.medium != 'digital'
            or loan.state != 'active' or not loan.id or not loan.record_id
            or loan.identity_id != account.identity_id
            or loan.source_credential != account.credential_id
            or loan.source_account_id != account.unique_id):
        return {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'}
    ref = hashlib.sha256('\0'.join((account.identity_id, account.credential_id,
                                      account.unique_id, loan.id, loan.record_id)).encode()).hexdigest()
    for row in service.storage.acquisitions(provider_id='manual', status='complete'):
        # Inspect only a record explicitly bound to this identity and loan.
        # All other manual acquisitions may contain unrelated personal files:
        # do not stat, open or parse them on each BnF synchronization.
        if row.get('identity_id') != account.identity_id:
            continue
        meta = row.get('metadata') or {}
        if type(meta) is not dict or meta.get('bnf_auto_ref') != ref:
            continue
        path = Path(str(meta.get('path') or ''))
        try:
            usable = path.is_file() and not path.is_symlink() and inspect_artifact(path).valid is True
        except (OSError, ValueError):
            usable = False
        return ({'status': 'unchanged'} if usable else
                {'status': 'attention_required', 'code': 'bnf_local_artifact_invalid'})
    try:
        session = service._sessions.get(service._session_key('bnf-pret', account.credential_id))
        epoch = service._account_context_epoch
        provider_epoch = getattr(session, '_epoch', None)

        def current():
            return (enabled(service, account) and service._account_context_epoch == epoch
                    and service._sessions.get(service._session_key('bnf-pret', account.credential_id)) is session
                    and getattr(session, '_epoch', None) == provider_epoch
                    and session.connected is True
                    and sum(row is account for row in session.accounts) == 1
                    and service._account_for_loan(loan) is account)

        if not current():
            return {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'}
        if control is not None:
            control.check_cancelled()
        directory = service._acquisition_work_dir(account.identity_id, 'bnf-pret', loan.id)
        if not current():
            return {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'}
        # The opt-in is a durable affirmative action. The provider itself
        # re-reads the exact account/loan and rejects missing signed actions.
        candidate, url = session.download_digital(
            loan, account, directory, explicit_user_action=True)
        if not current():
            return {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'}
        if (not isinstance(candidate, Path) or type(url) is not str or url
                or candidate.is_symlink() or directory.is_symlink()
                or candidate.parent.resolve() != directory.resolve()
                or not _valid_loan_token(candidate, inspect_artifact)):
            return {'status': 'attention_required', 'code': 'bnf_loan_token_invalid'}
        if control is not None:
            control.check_cancelled()
        if not current():
            return {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'}
        from .bnf_import_provenance import observed_loan, remember_import
        display_scope = observed_loan(loan)
        result = service.import_acsm(candidate, identity_id=account.identity_id,
                                     provider_id='manual', placement_provider_id='bnf-pret',
                                     title=loan.title)
        if result.get('status') in {'complete', 'unchanged'}:
            existing = service.storage.acquisition_get(result['acquisition_id'])
            meta = (existing or {}).get('metadata') or {}
            previous_ref = meta.get('bnf_auto_ref')
            # One identical ACSM reused across different upstream loans is
            # ambiguous; never overwrite the first account/loan binding.
            if previous_ref not in (None, ref):
                return {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'}
            produced = Path(str(result.get('path') or ''))
            if not produced.is_file() or produced.is_symlink() or inspect_artifact(produced).valid is not True:
                return {'status': 'attention_required', 'code': 'bnf_local_artifact_invalid'}
            service.storage.acquisition_update_state(
                result['acquisition_id'], 'complete', metadata={'bnf_auto_ref': ref})
            remember_import(service, result, display_scope)
        return {'status': result.get('status', 'attention_required')}
    except Cancelled:
        raise
    except Exception:
        return {'status': 'attention_required', 'code': 'bnf_auto_acquisition_failed'}
