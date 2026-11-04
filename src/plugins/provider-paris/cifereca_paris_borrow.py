"""Paris manual borrow protocol; persistence and admission belong to Core."""
from dataclasses import replace
import logging

from cifereca.plugin_api import (
    Account, SearchResult, LendingActionAdmission, ProviderCapabilityError,
    ProviderConfigurationError, BorrowRequestNotSubmitted, Loan, Cancelled,
)


def borrow_with_claim(session, item, account, *, admission, claimed_receipt,
                      confirmed, core_scope_current):
    if (confirmed is not True or type(admission) is not LendingActionAdmission
            or (admission.operation, admission.origin, admission.status) != (
                "loan.borrow", "manual", "admitted")
            or type(claimed_receipt) is not dict
            or claimed_receipt.get("schema") != 1
            or claimed_receipt.get("operation") != "loan.borrow"
            or claimed_receipt.get("status") != "request_sent"
            or type(claimed_receipt.get("operation_id")) is not str
            or not 0 < len(claimed_receipt["operation_id"]) <= 128
            or not callable(core_scope_current)):
        raise ProviderCapabilityError("paris_borrow_not_authorized")
    with session._loan_inventory_lock:
        if (type(account) is not Account or type(item) is not SearchResult
                or account.unique_id != "" or account.is_primary is not True
                or not account.identity_id
                or (account.provider_id, account.credential_id) != (
                    session.provider_id, session.credential_id)
                or (item.source_provider, item.source_credential) != (
                    session.provider_id, session.credential_id)
                or item.docbase != "DILICOM" or item.medium != "digital"
                or type(item.record_id) is not str or not item.record_id):
            raise ProviderConfigurationError("paris_borrow_scope_mismatch")
        borrower = (account.unique_id, account.identity_id,
                    account.provider_id, account.credential_id, account.is_primary)
        reference = (item.id, item.record_id, item.docbase, item.medium,
                     item.source_provider, item.source_credential, item.source_collection)
        client, broker = session.client, session.identity_broker

        def owner():
            if broker is None:
                return None
            from cifereca_paris import _core_account_owners
            refs = broker.accounts(borrower[1])
            if type(refs) not in (list, tuple):
                return None
            return _core_account_owners(
                refs, provider_id=borrower[2], credential_id=borrower[3],
                account_id=f"{borrower[2]}:{borrower[3]}:primary",
            )

        def current():
            try:
                return (
                    session.connected is True and session.client is client
                    and session.identity_broker is broker
                    and owner() == (borrower[1],)
                    and (session.provider_id, session.credential_id) == borrower[2:4]
                    and (account.unique_id, account.identity_id, account.provider_id,
                         account.credential_id, account.is_primary) == borrower
                    and len(live := [a for a in session._accounts if type(a) is Account
                        and (a.unique_id, a.identity_id, a.provider_id,
                             a.credential_id, a.is_primary) == borrower]) == 1
                    and live[0] is account
                    and (item.id, item.record_id, item.docbase, item.medium,
                         item.source_provider, item.source_credential,
                         item.source_collection) == reference
                    and core_scope_current() is True
                )
            except Exception:
                return False

        def require_current():
            if not current():
                raise ProviderConfigurationError("paris_borrow_scope_changed")

        operation = claimed_receipt["operation_id"]
        require_current()
        if operation in session._borrow_claim_attempts:
            raise ProviderCapabilityError("paris_borrow_claim_spent")

        def observed_loan(rows):
            matches = [row for row in rows if type(row) is Loan
                       and row.record_id == reference[1]]
            if not matches:
                return None
            if len(matches) != 1:
                raise ProviderCapabilityError("paris_borrow_loan_ambiguous")
            row = matches[0]
            raw = row.raw
            if (row.provider != "DILICOM" or row.medium != "digital"
                    or type(raw) is not dict
                    or type(raw.get("Id") or raw.get("HoldingId")) not in (str, int)
                    or type(raw.get("RecordId")) not in (str, int)
                    or str(raw.get("Id") or raw.get("HoldingId")) != row.id
                    or str(raw["RecordId"]) != reference[1]
                    or row.state in ("returned", "expired", "inactive")):
                raise ProviderCapabilityError("paris_borrow_loan_unverified")
            return replace(row, identity_id=borrower[1], source_provider=borrower[2],
                           source_credential=borrower[3], source_account_id="primary")

        # These GETs are descriptive only. The special unsent signal is
        # confined to this region; Core may CAS-release only this claim.
        try:
            rows = client.loans("DILICOM", "")
            require_current()
            existing = observed_loan(rows)
            if existing is not None:
                return existing
            payload = client._holdings_payload(
                "DILICOM", reference[1], item.scenario or "EBOOKS", item.title)
            require_current()
            href = client._pnb_loan_href(payload)
            require_current()
        except Cancelled:
            raise
        except Exception as exc:
            raise BorrowRequestNotSubmitted("paris_borrow_not_submitted") from exc

        # The first ASSARedirect GET may already create a loan. Spend the
        # in-session guard BEFORE dispatch; the durable Core claim is already
        # consumed. No exception below can release/replay that operation.
        session._borrow_claim_attempts.add(operation)
        logging.getLogger("cifereca").info(
            "TRACE paris pnb-loan phase=borrow_submitted")
        acsm_url = None
        try:
            acsm_url = client._resolve_pnb_loan_to_acsm(
                href, admission_current=current,
            )
        except Cancelled:
            raise
        except Exception:
            # Failure to obtain ACSM is independent of loan confirmation.
            # Only a fresh, exact ListLoans response can resolve this attempt.
            require_current()
        require_current()
        confirmed_loan = observed_loan(client.loans("DILICOM", ""))
        require_current()
        if confirmed_loan is None:
            raise ProviderCapabilityError("paris_borrow_result_unverified")
        if acsm_url:
            confirmed_loan.ebook_url = acsm_url
        logging.getLogger("cifereca").info(
            "TRACE paris pnb-loan phase=borrow_confirmed initial_acsm_offer=%s",
            bool(acsm_url),
        )
        return confirmed_loan
