"""Closed Core enrichment intent admission, independent of provider names.

Reuse the existing #4982 journal and specialised validators. An intent
is not a right, a transport grant, a new scheduler or a plugin API.
Existing loans use the bounded cache-only native repair port.
Import remains unsupported without a qualified native proof port.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..models import Loan
from ..plugin_api.provider_purchase_enrichment import PurchaseEnrichmentCandidate
from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot
from .notice_cover_repository import PersistedNoticeCover
from .notice_c_raw_input_receipt import PublicRawInputReceipt
from .notice_c_job_journal import JobRef


@dataclass(frozen=True, slots=True, repr=False)
class ExistingPurchaseIntent:
    snapshot: ProviderRightsSnapshot
    candidates: tuple[PurchaseEnrichmentCandidate, ...]
    context_current: Callable[[], bool] = field(compare=False)


@dataclass(frozen=True, slots=True, repr=False)
class CatalogueReparseIntent:
    source: PersistedNoticeCover
    receipt: PublicRawInputReceipt
    body: bytes
    implementation_digest: str
    expected_generation: int


@dataclass(frozen=True, slots=True, repr=False)
class ExistingLoanIntent:
    service: object = field(compare=False)
    rows: list[Loan] = field(compare=False)
    identity_id: str
    account_epoch: int
    source_epoch: int
    descriptors: object = field(compare=False)


NoticeEnrichmentIntent = ExistingPurchaseIntent | CatalogueReparseIntent | ExistingLoanIntent


@dataclass(frozen=True, slots=True)
class EnrichmentAdmission:
    refs: tuple[JobRef, ...]
    changed: bool | None


def request_notice_enrichment(storage, intent: NoticeEnrichmentIntent, *, now):
    """Admit only the exact supported typed trigger using its own validator.

    The catalogue port must not demand A; the purchase port must still
    require current committed A and separate policy. Raw bytes/locators
    are ephemeral inputs, never serialized by this dispatcher.
    """
    if type(intent) is ExistingPurchaseIntent:
        from .notice_c_purchase_intent import request_purchase_enrichment_intents
        refs, changed = request_purchase_enrichment_intents(
            storage, intent.snapshot, intent.candidates, now=now,
            context_current=intent.context_current, report_change=True,
        )
        return EnrichmentAdmission(refs, changed)
    if type(intent) is CatalogueReparseIntent:
        from .notice_c_reparse_requests import _request_public_reparse
        ref = _request_public_reparse(
            storage, intent.source, intent.receipt, intent.body,
            implementation_digest=intent.implementation_digest, now=now,
            expected_generation=intent.expected_generation,
        )
        # The existing cache-only port returns the authoritative job ref,
        # not a change notification. Do not guess its dedup state.
        return EnrichmentAdmission((ref,), None)
    if type(intent) is ExistingLoanIntent:
        from .notice_c_loan_enrichment import request_existing_loan_cache_repairs
        # The existing worker owns its storage and context fence. Never
        # dispatch an intent to a different journal or transfer authority.
        if getattr(intent.service, "storage", None) is not storage:
            return EnrichmentAdmission((), None)
        refs = request_existing_loan_cache_repairs(
            intent.service, intent.rows, identity_id=intent.identity_id,
            account_epoch=intent.account_epoch, source_epoch=intent.source_epoch,
            descriptors=intent.descriptors, now=now,
        )
        return EnrichmentAdmission(refs, None)
    raise ValueError("notice_enrichment_trigger_unsupported")
