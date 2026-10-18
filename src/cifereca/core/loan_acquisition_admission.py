"""Read-only source admission for the automatic-loan acquisition boundary.

Resolve only the existing connection owner, borrower guard, cross-identity
ledger conflict and entitlement selection. Never inspect files, acquire content,
mutate storage or infer the owner from the current UI identity. The facade keeps
all locks, artifact decisions and the later pre-ADEPT borrower revalidation.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from ..models import Loan
from .loan_safe_trace import loan_trace_ref


@dataclass(frozen=True, slots=True, repr=False)
class AutomaticLoanAdmission:
    identity: Any = None
    entitlement_id: str | None = None
    refusal: dict[str, Any] | None = None


def resolve(self, loan: Loan, *, _TRACE_LOG) -> AutomaticLoanAdmission:
    request_ref = loan_trace_ref(loan, None)
    bound_identity_id = self.storage.identity_for_connection(
        loan.source_provider, loan.source_credential
    )
    identity = self.identity_by_id(bound_identity_id)
    if identity is None:
        _TRACE_LOG.warning(
            "TRACE download loan_auto blocked reason=unbound_connection_identity request_ref=%s",
            request_ref,
        )
        return AutomaticLoanAdmission(refusal={
            "status": "attention_required", "loan_id": loan.id,
            "code": "adept_identity_unproven",
            "reason": "unbound_connection_identity",
            "retry": "explicit_only",
        })
    trace_ref = loan_trace_ref(loan, identity.identity_id)
    _TRACE_LOG.info("TRACE download identity resolved request_ref=%s loan_ref=%s", request_ref, trace_ref)
    if str(loan.source_provider).lower() == "paris":
        _, borrower_code = self._paris_adept_identity_for_loan(
            loan, connection_identity_id=identity.identity_id,
        )
        if borrower_code:
            _TRACE_LOG.warning(
                "TRACE download loan_auto blocked reason=borrower_binding_guard loan_ref=%s",
                trace_ref,
            )
            return AutomaticLoanAdmission(refusal={
                "status": "attention_required", "loan_id": loan.id,
                "code": borrower_code, "retry": "explicit_only",
            })
    entitlement_inventory = self.storage.entitlements(
        provider_id=loan.source_provider, state="active"
    )
    if str(loan.source_provider).lower() == "paris" and self._cross_identity_loan_conflict(
        self.storage.acquisitions(provider_id=loan.source_provider),
        provider_loan_id=loan.id, identity_id=identity.identity_id,
        entitlements=entitlement_inventory,
    ):
        _TRACE_LOG.warning(
            "TRACE download loan_auto blocked reason=cross_identity_loan loan_ref=%s",
            trace_ref,
        )
        return AutomaticLoanAdmission(refusal={
            "status": "attention_required", "loan_id": loan.id,
            "code": "adept_loan_identity_conflict",
            "reason": "cross_identity_loan", "retry": "explicit_only",
        })
    entitlement_rows = [
        row for row in entitlement_inventory
        if row.get("loan_id") == loan.id and row.get("identity_id") == identity.identity_id
    ]
    entitlement_id = entitlement_rows[0]["entitlement_id"] if entitlement_rows else None
    return AutomaticLoanAdmission(identity=identity, entitlement_id=entitlement_id)
