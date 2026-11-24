"""Paris-specific selected PNB offer and guarded staging operations."""
from __future__ import annotations

import os
from pathlib import Path
from dataclasses import replace
from cifereca.plugin_api import (
    Account, Loan, LoanInventoryScope, LoanInventorySnapshot,
    ProviderCapabilityError, ProviderRightsSnapshot,
)


class ParisSelectedDeliveryOperations:
    def rights_selected_delivery_offer_snapshot(
        self, account: Account, *, edition_id: str, right_id: str,
        kind: str, branch: str, explicit_user_action: bool = False,
    ) -> ProviderRightsSnapshot:
        """Exact scoped PNB action; observational, never a content GET."""
        from cifereca_paris import _TRACE_LOG
        if (explicit_user_action is not True or type(account) is not Account
                or kind != "loan" or branch != "dilicom"
                or type(edition_id) is not str or not edition_id
                or type(right_id) is not str or not right_id
                or len(edition_id) > 256 or len(right_id) > 256):
            raise ProviderCapabilityError("paris_selected_offer_not_admitted")
        lease = self._current_source_sync_offer_lease(account)
        if self._source_sync_offer_lease.get() is not None and lease is None:
            raise ProviderCapabilityError("paris_source_sync_lease_revoked")
        cached = lease.get("rights") if lease is not None else None
        if (type(cached) is ProviderRightsSnapshot
                and cached.status == "success"
                and cached.coverage == "complete"
                and cached.scope == self._downloadable_scope(account)):
            snapshot = cached
            _TRACE_LOG.info(
                "TRACE paris selected offer proof=previously_attested "
                "reobservation_attempted=false reason_code=same_cycle_exact",
            )
        else:
            snapshot = self.rights_observation_snapshot(account)
            _TRACE_LOG.info(
                "TRACE paris selected offer proof=fresh_observation "
                "reobservation_attempted=true reason_code=unscoped_or_unproven",
            )
        right = snapshot.exact(edition_id, right_id, "loan")
        if right is None or not right.provider_attested:
            # A failed or incomplete private inventory is not evidence that
            # this already-attested loan disappeared or was revoked.
            reason = ("paris_selected_offer_observation_unavailable"
                      if snapshot.coverage != "complete"
                      else "paris_selected_offer_unobserved")
            raise ProviderCapabilityError(reason)
        # State='unknown' is descriptive. The PNB action is independently
        # attested only when the fresh private ListLoans has EbookUrl.
        return replace(snapshot, rights=(right,))

    def rights_selected_delivery_candidate(
        self, account: Account, *, edition_id: str, right_id: str,
        kind: str, branch: str, delivery_offer_id: str,
        target_dir, explicit_user_action: bool = False,
        admission_current=None,
    ) -> tuple[Path, str]:
        """Reobserve the exact PNB link, then stage a file via the guarded GET.

        No PNB borrow/quota fallback: download_digital's selected-right mode
        requires the *current* matching DILICOM card and its direct EbookUrl.
        The result remains a candidate, not a Core receipt or OriginLink.
        """
        from cifereca_paris import (
            _paris_core_admission_current, _pnb_direct_url_observed,
            _paris_pnb_acsm_route,
        )
        route_witness = self._selected_acsm_route.get()
        self._selected_acsm_route.set(None)
        if (explicit_user_action is not True or type(account) is not Account
                or kind != "loan" or branch != "dilicom"
                or type(edition_id) is not str or not edition_id
                or type(right_id) is not str or not right_id
                or type(delivery_offer_id) is not str
                or not delivery_offer_id.startswith("paris.pnb:")
                or len(delivery_offer_id) != len("paris.pnb:") + 64
                or any(c not in "0123456789abcdef"
                       for c in delivery_offer_id[len("paris.pnb:"):])):
            raise ProviderCapabilityError("paris_candidate_not_admitted")
        if route_witness is not None and route_witness[:4] != (
                self._route_witness_scope(account), edition_id, right_id, delivery_offer_id):
            raise ProviderCapabilityError("paris_candidate_route_changed")
        if not _paris_core_admission_current(admission_current):
            raise ProviderCapabilityError("paris_core_admission_revoked")
        directory = Path(target_dir)
        try:
            if (not directory.is_dir() or directory.is_symlink()
                    or directory.resolve(strict=True) != directory.absolute()):
                raise ProviderCapabilityError("paris_candidate_staging_invalid")
        except (OSError, RuntimeError):
            raise ProviderCapabilityError("paris_candidate_staging_invalid") from None
        try:
            stage_fd = os.open(
                directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
        except OSError:
            raise ProviderCapabilityError("paris_candidate_staging_invalid") from None
        try:
            observed = self.rights_selected_delivery_offer_snapshot(
                account, edition_id=edition_id, right_id=right_id,
                kind=kind, branch=branch, explicit_user_action=True,
            )
            selected = observed.exact(edition_id, right_id, "loan")
            if (selected is None or not selected.delivery_attested
                    or selected.delivery_offer_id != delivery_offer_id):
                raise ProviderCapabilityError("paris_candidate_offer_unavailable")
            # Preserve the same exact account/borrower binding as the observation.
            # A fully attested A→B Core source cycle can use its exact typed
            # inventory; independent/manual requests must do a fresh GET.
            # Existing download_digital and supplier host/token guards still
            # revalidate scope and candidate before *content* GET.
            with self._loan_inventory_lock:
                lease = self._current_source_sync_offer_lease(account)
                if self._source_sync_offer_lease.get() is not None and lease is None:
                    raise ProviderCapabilityError("paris_source_sync_lease_revoked")
                old_rights = lease.get("rights") if lease is not None else None
                old_inventory = lease.get("inventory") if lease is not None else None
                prior = (
                    old_rights.exact(edition_id, right_id, "loan")
                    if type(old_rights) is ProviderRightsSnapshot
                    else None
                )
                if (type(old_inventory) is LoanInventorySnapshot
                        and old_inventory.status == "success"
                        and old_inventory.scope == LoanInventoryScope.for_account(account, descriptor=self.descriptor)
                        and old_inventory.branch_claims_complete("dilicom", "digital")
                        and old_inventory.branch_claims_complete("syracuse", "physical")
                        and prior is not None and prior.delivery_attested
                        and prior.delivery_offer_id == delivery_offer_id):
                    inventory = old_inventory
                    _TRACE_LOG.info(
                        "TRACE paris candidate proof=previously_attested "
                        "reobservation_attempted=false reason_code=same_cycle_exact",
                    )
                else:
                    inventory = self._snapshot_for_account(account)
                    _TRACE_LOG.info(
                        "TRACE paris candidate proof=fresh_observation "
                        "reobservation_attempted=true reason_code=unscoped_or_unproven",
                    )
                if not _paris_core_admission_current(admission_current):
                    raise ProviderCapabilityError("paris_core_admission_revoked")
                candidates = []
                for loan in inventory.loans:
                    if (type(loan) is not Loan or loan.provider != "DILICOM"
                            or loan.medium != "digital"
                            or loan.id != right_id or loan.record_id != edition_id
                            or loan.source_provider != self.provider_id
                            or loan.source_credential != self.credential_id
                            or loan.source_account_id != (account.unique_id or "primary")
                            or loan.identity_id != account.identity_id
                            or type(loan.raw) is not dict
                            or not _pnb_direct_url_observed(loan.ebook_url, client=self.client)):
                        continue
                    raw_right = loan.raw.get("Id") or loan.raw.get("HoldingId")
                    raw_edition = loan.raw.get("RecordId")
                    if (type(raw_right) not in (str, int)
                            or type(raw_edition) not in (str, int)
                            or str(raw_right) != right_id
                            or str(raw_edition) != edition_id):
                        continue
                    candidates.append(loan)
                if len(candidates) != 1:
                    raise ProviderCapabilityError("paris_candidate_source_unobserved")
                # The final selected inventory can change transport class
                # even while the borrower's exact right stays the same.
                # Do not bypass an early ADEPT check (or silently reuse an
                # obsolete ACSM route) when the current card changes.
                if route_witness is not None and (
                        self._route_witness_scope(account) != route_witness[0]
                        or _paris_pnb_acsm_route(candidates[0], client=self.client) is not route_witness[4]):
                    raise ProviderCapabilityError("paris_candidate_route_changed")
                # Another fresh ListLoans inside download_digital rejects a
                # disappeared link, rebind or loan mismatch before any PNB GET.
                pin = self._selected_delivery_inventory.set(inventory)
                try:
                    return self.download_digital(
                        candidates[0], account, directory,
                        expected_right_id=right_id,
                        expected_identity_id=account.identity_id,
                        selected_stage_fd=stage_fd,
                        **({"admission_current": admission_current}
                           if admission_current is not None else {}),
                    )
                finally:
                    self._selected_delivery_inventory.reset(pin)

        finally:
            os.close(stage_fd)

