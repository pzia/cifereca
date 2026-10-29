"""Account-scoped opt-in BnF loan materialization during Core synchronization."""
from __future__ import annotations

from .source_context import source_context_epoch

import hashlib
import logging
from pathlib import Path

from ..artifact_inspection import inspect_artifact
from ..models import Account, Loan
from ..plugin_api import Cancelled
from .bnf_explicit_acsm import _discard_invalid_candidate, _valid_loan_token


_TRACE_LOG = logging.getLogger("cifereca")


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


def _receipt_local_file_verified(service, account: Account, row: dict, path: Path) -> bool:
    """A private auto-ref is only a hint, never proof of an unchanged book.

    Verify that the complete acquisition still names the exact BnF directory,
    indexed placement and original physical SHA. No provider/DRM I/O.
    """
    from ..library_files import sha256_file
    try:
        if (type(row) is not dict or row.get('identity_id') != account.identity_id
                or row.get('provider_id') != 'manual'
                or row.get('credential_id') != 'manual'
                or type(row.get('artifact_id')) is not str):
            return False
        expected_id = row['artifact_id']
        artifact = service.storage.artifact_get(expected_id)
        if (type(artifact) is not dict or type(artifact.get('sha256')) is not str
                or expected_id != 'sha256:' + artifact['sha256']
                or type(artifact.get('size')) is not int
                or artifact['size'] <= 0):
            return False
        root = service.library_identity_provider_dir(account.identity_id, 'bnf-pret')
        if root.is_symlink() or path.is_symlink() or not path.is_file():
            return False
        # Reject ALL symlinks below the identity/provider directory, not just
        # the last component. An external EPUB must never satisfy this loan.
        relative = path.relative_to(root)
        if len(relative.parts) < 2:
            return False
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return False
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
        linked = service.storage.placement_for_path(path)
        if (type(linked) is not dict
                or linked.get('state') != 'present'
                or linked.get('identity_id') != account.identity_id
                or linked.get('provider_id') != 'bnf-pret'
                or linked.get('artifact_id') != expected_id):
            return False
        before = path.stat(follow_symlinks=False)
        if before.st_size != artifact['size'] or sha256_file(path) != artifact['sha256']:
            return False
        inspected = inspect_artifact(path)
        after = path.stat(follow_symlinks=False)
        if (inspected.valid is not True
                or inspected.media_format not in {'epub', 'pdf'}
                or any(getattr(before, key) != getattr(after, key)
                       for key in ('st_dev', 'st_ino', 'st_size',
                                   'st_mtime_ns', 'st_ctime_ns'))):
            return False
        return True
    except (OSError, ValueError, TypeError, KeyError):
        _TRACE_LOG.warning(
            "TRACE bnf auto receipt file check failed provider_id=%s "
            "identity_id=%s credential_id=%s",
            account.provider_id, account.identity_id, account.credential_id,
            exc_info=True,
        )
        return False


def materialize(service, account: Account, loan: Loan, *, control=None,
                physical_scan_cache: dict | None = None) -> dict:
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
    # Pin the exact Core account and provider session before either reading a
    # prior local receipt or fetching a new signed token. Consent is a lease:
    # a subsequent A→B→A rebind or provider reconnect cannot inherit it.
    source_scope = (
        account.identity_id, account.credential_id, account.unique_id,
        loan.id, loan.record_id,
        loan.source_provider, loan.source_credential, loan.source_account_id,
        loan.identity_id, loan.medium, loan.state,
    )
    core_epoch = source_context_epoch(service)
    consent_revision = service.storage.metadata_get(policy_key(account) + '.revision')
    session = service._sessions.get(
        service._session_key('bnf-pret', account.credential_id),
    )
    provider_epoch = getattr(session, '_epoch', None)

    def current() -> bool:
        try:
            return (
                enabled(service, account)
                and service.storage.metadata_get(
                    policy_key(account) + '.revision'
                ) == consent_revision
                and source_context_epoch(service) == core_epoch
                and service._sessions.get(
                    service._session_key('bnf-pret', account.credential_id),
                ) is session
                and getattr(session, '_epoch', None) == provider_epoch
                and session.connected is True
                and sum(row is account for row in session.accounts) == 1
                and (
                    account.identity_id, account.credential_id,
                    account.unique_id, loan.id, loan.record_id,
                    loan.source_provider, loan.source_credential,
                    loan.source_account_id, loan.identity_id, loan.medium,
                    loan.state,
                ) == source_scope
                and loan.source_provider == 'bnf-pret'
                and loan.source_credential == account.credential_id
                and loan.source_account_id == account.unique_id
                and loan.identity_id == account.identity_id
                and loan.medium == 'digital'
                and loan.state == 'active'
                and service._account_for_loan(loan) is account
            )
        except Exception:
            return False

    if not current():
        return {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'}
    manual_acquisitions = service.storage.acquisitions(provider_id='manual', status='complete')
    for row in manual_acquisitions:
        # Inspect only a record explicitly bound to this identity and loan.
        # All other manual acquisitions may contain unrelated personal files:
        # do not stat, open or parse them on each BnF synchronization.
        if row.get('identity_id') != account.identity_id:
            continue
        meta = row.get('metadata') or {}
        if type(meta) is not dict or meta.get('bnf_auto_ref') != ref:
            continue
        path = Path(str(meta.get('path') or ''))
        usable = _receipt_local_file_verified(service, account, row, path)
        if not current():
            return {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'}
        return ({'status': 'unchanged'} if usable else
                {'status': 'attention_required', 'code': 'bnf_local_artifact_invalid'})

    # A user-confirmed declaration is separate from an ADEPT receipt.
    # Require the exact current right AND loan transaction to match, and
    # revalidate the existing file without granting any supplier provenance.
    # This check runs while the caller holds the acquisition lock, before
    # any new signed GET. A stale declaration never authorizes downloading.
    try:
        from .library_declared_links import has_active_declared_link
        declared_scope = dict(
            identity_id=account.identity_id, provider_id=loan.source_provider,
            credential_id=account.credential_id, account_id=account.unique_id,
            item_id=loan.record_id,
        )
        declared = has_active_declared_link(service.storage, **declared_scope)
    except Cancelled:
        raise
    except Exception:
        _TRACE_LOG.warning(
            "TRACE bnf auto local evidence failed stage=declared_lookup "
            "provider_id=%s credential_id=%s account_id=%s loan_id=%s",
            loan.source_provider, account.credential_id,
            account.unique_id, loan.id, exc_info=True,
        )
        return {'status': 'attention_required', 'code': 'bnf_local_evidence_unavailable'}
    if not current():
        return {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'}
    if declared:
        try:
            from .library_declared_presence import verify_declared_presence
            presence = verify_declared_presence(
                service, **declared_scope, source_current=current,
                expected_loan_id=loan.id, _acquisition_lock_held=True,
            )
        except Cancelled:
            raise
        except Exception:
            _TRACE_LOG.warning(
                "TRACE bnf auto local evidence failed stage=declared_revalidation "
                "provider_id=%s credential_id=%s account_id=%s loan_id=%s",
                loan.source_provider, account.credential_id,
                account.unique_id, loan.id, exc_info=True,
            )
            return {'status': 'attention_required', 'code': 'bnf_local_evidence_unavailable'}
        if not current():
            return {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'}
        if presence.status == 'declared_current':
            return {'status': 'declared_local'}
        return {'status': 'attention_required', 'code': 'bnf_local_declaration_needs_review'}

    # An older manual ACSM/EPUB or indexed manual placement may already
    # represent this title. Titles are *hints*, not proof of the same edition
    # or licence: stop before a new signed GET and request a deliberate
    # reconciliation instead of silently producing a second file. The
    # provider-agnostic screen never opens, attaches or moves any EPUB.
    try:
        from .library_audit_roots import configured_identity_roots
        from .local_candidate_screen import screen_local_candidates
        # Older manually imported ACSM books can live in either directory.
        # Both are possible presence hints only: neither folder name proves
        # the edition, BnF loan, ADEPT licence or authorization to attach it.
        roots = tuple(
            root / directory
            for root in configured_identity_roots(
                service.config, account.identity_id,
            )[account.identity_id]
            for directory in ('manual', 'acsm-import')
        )
        catalogue = service.storage.catalog_entries(
            state='local', identity_id=account.identity_id, limit=8193,
        )
        local = screen_local_candidates(
            identity_id=account.identity_id, title=loan.title,
            roots=roots, acquisitions=manual_acquisitions, catalogue=catalogue,
        )
    except Cancelled:
        raise
    except Exception:
        _TRACE_LOG.warning(
            "TRACE bnf auto local evidence failed stage=indexed_candidates "
            "provider_id=%s credential_id=%s account_id=%s loan_id=%s",
            loan.source_provider, account.credential_id,
            account.unique_id, loan.id, exc_info=True,
        )
        return {'status': 'attention_required', 'code': 'bnf_local_evidence_unavailable'}
    if not current():
        return {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'}
    if local.status == 'needs_reconciliation':
        if local.reason != 'local_candidate_found':
            # Broken/inconclusive local evidence is a real integrity gate.
            # A title match on otherwise readable local bytes is NOT.
            return {'status': 'attention_required',
                    'code': 'bnf_local_evidence_unavailable'}
        _TRACE_LOG.info(
            "TRACE bnf local bibliographic hint is not a Sync veto "
            "provider_id=%s credential_id=%s account_id=%s loan_id=%s",
            account.provider_id, account.credential_id,
            account.unique_id, loan.id,
        )

    # An old manually imported EPUB may not have any SQLite reference.
    # Consult only configured manual/ roots via Core's bounded, no-follow
    # physical inventory. OPF title equality is a REVIEW HINT, never a loan
    # or ADEPT grant. A partial/unsafe scan must not certify absence.
    try:
        from .local_physical_title_candidates import observe_physical_local_titles
        cache_key = (account.identity_id, roots, core_epoch, consent_revision,
                     provider_epoch)
        physical = (
            physical_scan_cache.get(cache_key)
            if type(physical_scan_cache) is dict else None
        )
        if physical is None:
            # Preserve the existing cancellation checkpoints around the
            # signed GET and before ADEPT. The scan itself has a hard deadline;
            # extra polling here would shift a post-GET cancellation into the
            # pre-GET phase and invalidate the cleanup contract.
            physical = observe_physical_local_titles(
                identity_id=account.identity_id, roots=roots,
            )
            if type(physical_scan_cache) is dict:
                physical_scan_cache[cache_key] = physical
        physical_result = physical.screen(
            identity_id=account.identity_id, title=loan.title,
        )
    except Cancelled:
        raise
    except Exception:
        _TRACE_LOG.warning(
            "TRACE bnf auto local evidence failed stage=physical_candidates "
            "provider_id=%s credential_id=%s account_id=%s loan_id=%s",
            loan.source_provider, account.credential_id,
            account.unique_id, loan.id, exc_info=True,
        )
        return {'status': 'attention_required', 'code': 'bnf_local_evidence_unavailable'}
    if not current():
        return {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'}
    if physical_result.status == 'needs_reconciliation':
        if physical_result.reason != 'local_candidate_found':
            return {'status': 'attention_required',
                    'code': 'bnf_local_evidence_unavailable'}
        # The OPF title can confirm a bibliographic candidate but it never
        # proves a provider OriginLink, the current ACSM or ADEPT identity.
        # A previously configured, fresh account-scoped opt-in is therefore
        # allowed to reach the independently signed GET as in #3171, never
        # to adopt, unlink, relocate or delete the historical manual EPUB.
        _TRACE_LOG.info(
            "TRACE bnf local OPF hint is not a Sync veto "
            "provider_id=%s credential_id=%s account_id=%s loan_id=%s",
            account.provider_id, account.credential_id,
            account.unique_id, loan.id,
        )
    # A signed ACSM is a private short-lived candidate. If admission fails
    # before ADEPT is invoked, it must not linger in acquisition-work.
    # Once handed to import_acsm, leave lifetime and receipt ownership to
    # that transaction (including failed/retryable fulfillments).
    candidate = None
    directory = None
    handed_to_adept = False
    try:
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
        handed_to_adept = True
        result = service.import_acsm(candidate, identity_id=account.identity_id,
                                     provider_id='manual', placement_provider_id='bnf-pret',
                                     title=loan.title)
        # ADEPT fulfillment can change the Core session, identity assignment
        # or the explicit per-account policy while running. It may already
        # have created a receipt: never claim the old borrower on that receipt.
        if not current():
            return {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'}
        if result.get('status') in {'complete', 'unchanged'}:
            existing = service.storage.acquisition_get(result['acquisition_id'])
            meta = (existing or {}).get('metadata') or {}
            previous_ref = meta.get('bnf_auto_ref')
            # One identical ACSM reused across different upstream loans is
            # ambiguous; never overwrite the first account/loan binding.
            if previous_ref not in (None, ref):
                return {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'}
            produced = Path(str(result.get('path') or ''))
            # An ADEPT "complete" without a matching indexed artifact and
            # provider-owned placement is not a usable local book.
            usable = _receipt_local_file_verified(service, account, existing, produced)
            if not current():
                return {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'}
            if not usable:
                return {'status': 'attention_required', 'code': 'bnf_local_artifact_invalid'}
            service.storage.acquisition_update_state(
                result['acquisition_id'], 'complete', metadata={'bnf_auto_ref': ref})
            if not current():
                return {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'}
            remember_import(service, result, display_scope)
        return ({'status': result.get('status', 'attention_required')}
                if current() else
                {'status': 'attention_required', 'code': 'bnf_loan_scope_unproven'})
    except Cancelled:
        raise
    except Exception:
        _TRACE_LOG.warning(
            "TRACE bnf auto acquisition failed stage=delivery_or_import "
            "provider_id=%s credential_id=%s account_id=%s loan_id=%s",
            loan.source_provider, account.credential_id,
            account.unique_id, loan.id, exc_info=True,
        )
        return {'status': 'attention_required', 'code': 'bnf_auto_acquisition_failed'}
    finally:
        # Delete solely our original staging .acsm, never a provider path,
        # an outside symlink or a file already admitted to ADEPT.
        if not handed_to_adept and directory is not None:
            _discard_invalid_candidate(candidate, directory)
