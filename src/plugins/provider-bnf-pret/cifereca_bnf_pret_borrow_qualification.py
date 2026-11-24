"""Read-only borrower qualification; no borrow admission or mutation."""
from __future__ import annotations
from dataclasses import dataclass
from time import time
from cifereca.plugin_api import Account, SearchResult, ProviderConfigurationError, ProviderCapabilityError, ProviderError
from cifereca_bnf_pret_http import ReaderHTTPError
from cifereca_bnf_pret_web import WebParseError
from cifereca_bnf_pret_catalog import resource_url

@dataclass(frozen=True, slots=True, repr=False)
class BorrowerInformationalObservation:
    """Ephemeral display candidate, not a Core admission evidence type."""
    identity_id: str
    provider_id: str
    credential_id: str
    borrower_id: str
    record_id: str
    source_collection: str | None
    session_epoch: str
    observed_at: int
    source_account_id: str | None = None
    pass_state: str = "unknown"
    remaining_quota: int | None = None
    available_copies: int | None = None
    item_offer: str = "unknown"
    permission: str = "unknown"


def borrower_eligibility_for(self, item: SearchResult, account: Account, *,
                             informational=False):
    """Fresh read-only *partial* borrower evidence for #2055.

    Real BnF pages currently do not attest the subscription state,
    remaining borrower quota or final borrow authorization separately.
    Never infer their positivity from successful authentication, a
    .button-borrow link or an observed CSRF form. Consequently this
    method NEVER returns an eligible positive four-way result.

    This intentionally unadvertised provider API is only preparation
    for a future, independently qualified source contract. No POST,
    no ACSM, no acquired right, no diagnostic payload containing IDs.
    """
    from cifereca.plugin_api import BorrowerOperationEvidence
    with self._lock:
        if (type(item) is not SearchResult
                or type(account) is not Account
                or item.source_provider != self.provider_id
                or item.source_credential != self.credential_id
                or item.docbase != 'BNF_PRET'
                or item.medium != 'digital'
                or type(item.record_id) is not str
                or not item.record_id or len(item.record_id) > 128
                or not self.connected or self._closed
                or len(self._accounts) != 1
                or account is not self._accounts[0]):
            raise ProviderConfigurationError('account_scope_mismatch')
        source_account = item.source_account_id
        def advisory_notice_current():
            return (
                (item.source_account_id is None or
                 (type(item.source_account_id) is str
                  and item.source_account_id == account.unique_id))
                and item.source_account_id == source_account
                and type(item.metadata) is dict
                and item.metadata.get("catalogue_access") != "shared_read_only"
                and item.metadata.get("search_provenance") != "known_catalog_stale"
            )

        if informational and not advisory_notice_current():
            raise ProviderConfigurationError('account_scope_mismatch')
        epoch = self._epoch
        borrower = (
            account.unique_id, account.identity_id,
            account.provider_id, account.credential_id, account.is_primary,
        )
        notice = (
            item.id, item.record_id, item.docbase, item.medium,
            item.source_provider, item.source_credential,
            item.source_collection,
        )
        if (borrower[2:] != (
                self.provider_id, self.credential_id, account.is_primary)
                or not borrower[1] or type(borrower[4]) is not bool):
            raise ProviderConfigurationError('account_scope_mismatch')
        broker = self.identity_broker
        owner = (self._core_owner_binding(borrower[0], borrower[1])
                 if broker is not None else None)
        if broker is not None and (owner is None or owner[1] != (borrower[1],)):
            raise ProviderConfigurationError('account_scope_mismatch')

        def current():
            return (
                not self._closed and self.connected is True
                and self._epoch == epoch
                and len(self._accounts) == 1
                and account is self._accounts[0]
                and self.identity_broker is broker
                and (not informational or advisory_notice_current())
                and (account.unique_id, account.identity_id,
                     account.provider_id, account.credential_id,
                     account.is_primary) == borrower
                and (item.id, item.record_id, item.docbase, item.medium,
                     item.source_provider, item.source_credential,
                     item.source_collection) == notice
                and self.provider_id == borrower[2]
                and self.credential_id == borrower[3]
                and (broker is None or
                     self._core_owner_binding(borrower[0], borrower[1]) == owner)
            )

        if not current():
            raise ProviderConfigurationError('account_scope_mismatch')
        # Existing positively observed loan is NOT a permission to
        # open a second one. Never interpret absence on a partial
        # activity page as a positive quota or availability signal.
        loans = self._read_live_loans_locked(account)
        if not current():
            raise ProviderConfigurationError('account_scope_mismatch')
        if any(loan.record_id == notice[1] for loan in loans):
            raise ProviderCapabilityError('borrow_existing_loan')
        try:
            from cifereca_bnf_pret import read_detail
            record = read_detail(self.client, notice[1])
        except (ReaderHTTPError, WebParseError) as exc:
            raise ProviderError(
                self._report_failure('borrow_eligibility', exc)) from None
        if not current() or record.resource_id != notice[1]:
            raise ProviderConfigurationError('account_scope_mismatch')
        # The sole qualified negative is the literal "unavailable"
        # marker *inside this exact book's microdata*. Missing marker
        # remains UNKNOWN, never an affirmative "available".
        if informational:
            return BorrowerInformationalObservation(
                identity_id=borrower[1], provider_id=borrower[2],
                credential_id=borrower[3], borrower_id=borrower[0],
                record_id=notice[1], source_collection=notice[6],
                session_epoch=epoch, observed_at=int(time()),
                source_account_id=source_account,
                item_offer=('unavailable' if record.availability == 'unavailable'
                            else 'unknown'),
            )
        return BorrowerOperationEvidence(
            identity_id=borrower[1], provider_id=borrower[2],
            credential_id=borrower[3], borrower_id=borrower[0],
            docbase=notice[2], record_id=notice[1],
            source_collection=notice[6], account_state='unknown',
            quota_state='unknown',
            item_offer=(
                'unavailable' if record.availability == 'unavailable'
                else 'unknown'
            ), permission='unknown',
        )

def advisory_display_values(self, observation, item: SearchResult, account: Account):
    """Project only display-safe values for the current exact session; no I/O."""
    with self._lock:
        if (type(observation) is not BorrowerInformationalObservation
                or type(item) is not SearchResult or type(account) is not Account
                or self._closed or not self.connected
                or len(self._accounts) != 1 or account is not self._accounts[0]
                or item.docbase != 'BNF_PRET' or item.medium != 'digital'
                or type(observation.observed_at) is not int
                or not 0 < observation.observed_at <= int(time())
                or (item.source_account_id is not None and
                    (type(item.source_account_id) is not str
                     or item.source_account_id != account.unique_id))
                or item.source_account_id != observation.source_account_id
                or type(item.metadata) is not dict
                or item.metadata.get("catalogue_access") == "shared_read_only"
                or item.metadata.get("search_provenance") == "known_catalog_stale"
                or (observation.identity_id, observation.provider_id,
                    observation.credential_id, observation.borrower_id)
                != (account.identity_id, account.provider_id,
                    account.credential_id, account.unique_id)
                or (self.provider_id, self.credential_id)
                != (account.provider_id, account.credential_id)
                or (item.source_provider, item.source_credential)
                != (account.provider_id, account.credential_id)
                or (observation.record_id, observation.source_collection,
                    observation.session_epoch)
                != (item.record_id, item.source_collection, self._epoch)):
            return None
        broker = self.identity_broker
        if broker is not None:
            owner = self._core_owner_binding(account.unique_id, account.identity_id)
            if owner is None or owner[1] != (account.identity_id,):
                return None
        return {
            "observed_at": observation.observed_at,
            "pass_state": "unknown",
            "remaining_quota": None,
            "available_copies": None,
            "item_offer": ("unavailable" if observation.item_offer == "unavailable"
                           else "unknown"),
            "permission": "unknown",
        }


def borrow_preflight(self, item: SearchResult, account: Account) -> dict:
    """Read-only inspection for #2055; never an authorization or POST.

    Deliberately not declared as acquisition.borrow or called by search,
    holdings, sync, startup or CoreUI. Future action admission must bind
    the user's explicit confirmation to this same live account/session.
    """
    from cifereca_bnf_pret_borrow import (confirmation_link, activation_form,
                                         _is_media_confirmation)
    with self._lock:
        if (type(item) is not SearchResult or type(account) is not Account
                or item.source_provider != self.provider_id
                or item.source_credential != self.credential_id
                or item.docbase != 'BNF_PRET' or item.medium != 'digital'
                or type(item.record_id) is not str
                or not item.record_id or len(item.record_id) > 128
                or not self.connected or self._closed
                or len(self._accounts) != 1 or account is not self._accounts[0]):
            raise ProviderConfigurationError('account_scope_mismatch')
        epoch = self._epoch
        scope = (account.unique_id, account.identity_id,
                 account.provider_id, account.credential_id)
        if scope[2:] != (self.provider_id, self.credential_id) or not scope[1]:
            raise ProviderConfigurationError('account_scope_mismatch')
        notice = (item.id, item.record_id, item.docbase, item.medium,
                  item.source_provider, item.source_credential,
                  item.source_collection)
        broker = self.identity_broker
        owner = (self._core_owner_binding(scope[0], scope[1])
                 if broker is not None else None)
        if broker is not None and (owner is None or owner[1] != (scope[1],)):
            raise ProviderConfigurationError('account_scope_mismatch')

        def current():
            return (not self._closed and self.connected and self._epoch == epoch
                    and len(self._accounts) == 1 and account is self._accounts[0]
                    and (account.unique_id, account.identity_id,
                         account.provider_id, account.credential_id) == scope
                    and (item.id, item.record_id, item.docbase, item.medium,
                         item.source_provider, item.source_credential,
                         item.source_collection) == notice
                    and self.provider_id == scope[2]
                    and self.credential_id == scope[3]
                    and self.identity_broker is broker
                    and (broker is None or
                         self._core_owner_binding(scope[0], scope[1]) == owner))

        # Fresh positive inventory is used only to reject a *known*
        # existing loan, never to infer a right to borrow from absence.
        if not current():
            raise ProviderConfigurationError('account_scope_mismatch')
        loans = self._read_live_loans_locked(account)
        if not current():
            raise ProviderConfigurationError('account_scope_mismatch')
        if any(loan.record_id == notice[1] for loan in loans):
            raise ProviderCapabilityError('borrow_existing_loan')
        try:
            target = resource_url(notice[1])
            actual, html = self.client.request('GET', target)
            if not current():
                raise ProviderConfigurationError('account_scope_mismatch')
            link = confirmation_link(
                html, page_url=actual, resource_id=notice[1])
            # The form GET is not assumed side-effect free. This
            # read-only preflight may inspect it but never visit it.
            if _is_media_confirmation(link):
                return {'status': 'unverified',
                        'reason_code': 'borrow_confirmation_effect_unknown'}
            actual, confirmation = self.client.borrow_confirmation(
                link, resource_id=notice[1])
            if not current():
                raise ProviderConfigurationError('account_scope_mismatch')
            # Do not return or log the action URL, CSRF or hidden fields.
            activation_form(confirmation, page_url=actual,
                            resource_id=notice[1])
            if not current():
                raise ProviderConfigurationError('account_scope_mismatch')
            return {'status': 'unverified',
                    'reason_code': 'borrow_confirmation_observed'}
        except (ReaderHTTPError, WebParseError) as error:
            raise ProviderError(self._report_failure('borrow_preflight', error)) from None

