"""Offline reference provider for the canonical A->B Core Sync (#3722).

This module supplies scoped positive observations and *staged candidate bytes*.
It never stores entitlements, grants consent, selects local files, imports DRM,
publishes an OriginLink/Placement, or invokes the convergence engine. It is
disabled by default and cannot access a network endpoint.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
from threading import RLock

from cifereca.plugin_api import (
    Account, ProviderCapabilities, ProviderCapabilityError,
    ProviderConfigurationError, ProviderDescriptor, ProviderPlugin,
    ProviderRightObservation, ProviderRightsScope, ProviderRightsSnapshot,
    ProviderSession,
)
from cifereca_reference_fixtures import ReferenceResource, epub_bytes, resources


BRANCH = "reference_fixture"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_OFFER = re.compile(r"reference\.offer:[0-9a-f]{64}\Z")
DESCRIPTOR = ProviderDescriptor(
    id="reference", display_name="Reference Provider — fictif",
    description="Contrat fournisseur reproductible et strictement hors réseau.",
    state="ready",
    capabilities=ProviderCapabilities(
        authentication=True, multiple_credentials=True, linked_accounts=True,
        # Selected delivery ABI is not the legacy library()/download_direct().
    ),
    notes=("Simulation CI uniquement ; aucun paiement, authentification ni droit réel.",
           "Le Core seul autorise l'acquisition et publie les fichiers canoniques."),
)


class ReferenceSession(ProviderSession):
    def __init__(self, *, identity_broker=None, **kwargs):
        super().__init__(descriptor=DESCRIPTOR, **kwargs)
        self.identity_broker = identity_broker
        self._lock = RLock()
        self._epoch = 0
        self._closed = False
        self._interrupted: set[tuple[str, str, str]] = set()
        self._before_write = None  # Optional deterministic race hook, only in tests.
        self._delivery_calls = 0
        scenario = self.options.get("scenario", "mixed")
        self._mode = self.options.get("accounts", "two")
        count = self.options.get("bulk_count", 350)
        self._page_size = self.options.get("page_size", 50)
        if (scenario not in ("mixed", "empty", "bulk")
                or self._mode not in ("one", "two")
                or type(self._page_size) is not int
                or not 1 <= self._page_size <= 100
                or type(count) is not int or not 1 <= count <= 500):
            raise ProviderConfigurationError("reference_invalid_fixture_options")
        self._resources = resources(scenario, bulk_count=count)
        self._by_id = {row.edition_id: row for row in self._resources}

    def connect(self) -> list[Account]:
        with self._lock:
            self._epoch += 1
            self._closed = False
            self._interrupted.clear()
            ids = ("reader-a",) if self._mode == "one" else ("reader-a", "reader-b")
            self._accounts = [
                Account(
                    display_name="Lecteur fictif " + key.upper(),
                    unique_id=key, provider_id=self.provider_id,
                    credential_id=self.credential_id, is_primary=(i == 0),
                ) for i, key in enumerate(ids)
            ]
            self.connected = True
            return list(self._accounts)

    def close(self) -> None:
        with self._lock:
            self._epoch += 1
            self._closed = True
            self.connected = False
            self._accounts = []

    @staticmethod
    def _coords(account: Account):
        return (account.identity_id, account.provider_id,
                account.credential_id, account.unique_id)

    def _bound(self, account: Account):
        """Require exactly one live account and its Core credential-qualified owner.

        A copied Account from the Core is fine; a changed borrower, foreign
        credential, ambiguous legacy v1 ref or missing identity broker is not.
        """
        if (type(account) is not Account or not self.connected or self._closed
                or account.provider_id != self.provider_id
                or account.credential_id != self.credential_id
                or type(account.identity_id) is not str or not account.identity_id):
            raise ProviderCapabilityError("reference_account_unverified")
        coords = self._coords(account)
        matches = [a for a in self._accounts
                   if type(a) is Account and self._coords(a) == coords]
        if len(matches) != 1 or self.identity_broker is None:
            raise ProviderCapabilityError("reference_account_unverified")
        try:
            broker_refs = self.identity_broker.accounts(account.identity_id)
        except Exception as exc:
            raise ProviderCapabilityError("reference_account_unverified") from None
        if type(broker_refs) not in (list, tuple):
            raise ProviderCapabilityError("reference_account_unverified")
        matching_refs = [
            ref for ref in broker_refs
            if getattr(ref, "provider_id", None) == self.provider_id
            and getattr(ref, "account_id", None) == account.unique_id
        ]
        if len(matching_refs) != 1:
            raise ProviderCapabilityError("reference_account_unverified")
        ref = matching_refs[0]
        if (getattr(ref, "scope_version", None) != 2
                or getattr(ref, "credential_id", None) != self.credential_id
                or getattr(ref, "identity_id", None) != account.identity_id):
            raise ProviderCapabilityError("reference_account_unverified")
        return matches[0], (self._epoch, id(matches[0]), id(self.identity_broker), coords)

    def _still_bound(self, account, pin):
        try:
            _, new_pin = self._bound(account)
            return pin == new_pin
        except ProviderCapabilityError:
            return False

    @staticmethod
    def _scope(account):
        return ProviderRightsScope(
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
        )

    @staticmethod
    def _observed():
        return datetime.now(timezone.utc).isoformat()

    def _offer_id(self, account: Account, row: ReferenceResource) -> str:
        # Opaque, scope- and session-qualified. Not a reusable right or token.
        # Even A->A replacement with identical account strings invalidates
        # the previous offer: this live session object is not the old owner.
        live = next(a for a in self._accounts if self._coords(a) == self._coords(account))
        payload = "\x00".join((*self._coords(account), BRANCH, row.edition_id,
                                row.kind, str(self._epoch), str(id(live))))
        return "reference.offer:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _observation(self, row, *, account, observed, qualified=False):
        delivery = row.delivery if qualified or row.delivery != "available" else "unknown"
        return ProviderRightObservation(
            edition_id=row.edition_id,
            right_id="right-" + row.edition_id,
            kind=row.kind, state=row.state,
            evidence_kind="provider_response",
            evidence_ref="reference.offline.fixture",
            observed_at=observed,
            delivery=delivery,
            delivery_offer_id=(
                self._offer_id(account, row) if delivery == "available" else ""
            ),
            delivery_unavailable_reason=(
                "archived" if row.archived else ""
            ),
        )

    def rights_observation_snapshot(self, account: Account, *, resume_cursor: str = ""):
        """Positive-only typed inventory, with bounded deterministic pagination.

        Pages are never negative authority, even after the last page.
        The optional cursor is additive; legacy callers read the first page.
        """
        with self._lock:
            _, pin = self._bound(account)
            if type(resume_cursor) is not str:
                raise ProviderCapabilityError("reference_invalid_cursor")
            if not resume_cursor:
                offset = 0
            elif re.fullmatch(r"offset:[1-9][0-9]{0,3}", resume_cursor):
                offset = int(resume_cursor[7:])
            else:
                raise ProviderCapabilityError("reference_invalid_cursor")
            if offset > len(self._resources) or (
                    offset and offset % self._page_size != 0):
                raise ProviderCapabilityError("reference_invalid_cursor")
            observed = self._observed()
            end = min(offset + self._page_size, len(self._resources))
            rows = tuple(self._observation(row, account=account, observed=observed)
                         for row in self._resources[offset:end])
            if not self._still_bound(account, pin):
                raise ProviderCapabilityError("reference_account_rebound")
            return ProviderRightsSnapshot(
                scope=self._scope(account), rights=rows,
                status="partial", coverage="partial",
                pagination_complete=end == len(self._resources),
                branch=BRANCH, observed_at=observed,
                snapshot_ref="reference.offline.fixture",
                resume_cursor=f"offset:{end}" if end < len(self._resources) else "",
            )

    def downloadable_resource_snapshot(self, account: Account, *, resume_cursor: str = ""):
        """Versioned ABI v2: resource capacity is separate from optional rights.

        No new inventory read or owner decision; the same positive-only scoped
        v1 facts are converted by the PUBLIC Core-owned compatibility adapter.
        """
        from cifereca.plugin_api import from_legacy_rights_snapshot

        snapshot = from_legacy_rights_snapshot(
            self.rights_observation_snapshot(account, resume_cursor=resume_cursor)
        )
        # A legitimate downloadable item can have NO right metadata at all.
        # This distinction is not representable in the legacy right envelope.
        return replace(snapshot, resources=tuple(
            replace(item, right=None) if item.source.edition_id == "epub-002"
            else item for item in snapshot.resources
        ))

    def downloadable_selected_offer_snapshot(
        self, account: Account, *, edition_id, resource_id, kind, branch,
        explicit_user_action=False,
    ):
        """Selected, exact positive/negative/unknown v2 capacity observation."""
        from cifereca.plugin_api import from_legacy_rights_snapshot

        snapshot = from_legacy_rights_snapshot(
            self.rights_selected_delivery_offer_snapshot(
                account, edition_id=edition_id, right_id=resource_id,
                kind=kind, branch=branch,
                explicit_user_action=explicit_user_action,
            )
        )
        return replace(snapshot, resources=tuple(
            replace(item, right=None) if item.source.edition_id == "epub-002"
            else item for item in snapshot.resources
        ))

    def downloadable_selected_delivery_candidate(
        self, account: Account, *, edition_id, resource_id, kind, branch,
        selected_offer_id, target_dir, explicit_user_action=False,
    ):
        """Return typed v2 staged bytes, never a Core publication receipt."""
        from cifereca.plugin_api import SourceReference, DeliveryCandidate

        path, hint = self.rights_selected_delivery_candidate(
            account, edition_id=edition_id, right_id=resource_id,
            kind=kind, branch=branch, delivery_offer_id=selected_offer_id,
            target_dir=target_dir, explicit_user_action=explicit_user_action,
        )
        return DeliveryCandidate(
            source=SourceReference(
                self._scope(account), branch, edition_id, resource_id, kind,
            ),
            selected_offer_id=selected_offer_id, path=path, provider_hint=hint,
        )

    def rights_selected_delivery_offer_snapshot(
        self, account: Account, *, edition_id, right_id, kind, branch,
        explicit_user_action=False,
    ):
        """Exact selected preflight, invoked ONLY after Core source consent."""
        with self._lock:
            _, pin = self._bound(account)
            if (explicit_user_action is not True or branch != BRANCH
                    or type(edition_id) is not str
                    or type(right_id) is not str
                    or type(kind) is not str
                    or right_id != "right-" + edition_id
                    or not _ID.fullmatch(edition_id)):
                raise ProviderCapabilityError("reference_selected_scope_mismatch")
            row = self._by_id.get(edition_id)
            if row is None or kind != row.kind:
                raise ProviderCapabilityError("reference_selected_unobserved")
            observed = self._observed()
            fact = self._observation(row, account=account, observed=observed,
                                     qualified=True)
            if not self._still_bound(account, pin):
                raise ProviderCapabilityError("reference_account_rebound")
            return ProviderRightsSnapshot(
                scope=self._scope(account), rights=(fact,),
                status="partial", coverage="partial", pagination_complete=False,
                branch=BRANCH, observed_at=observed,
                snapshot_ref="reference.offline.selected",
            )

    def rights_selected_delivery_candidate(
        self, account: Account, *, edition_id, right_id, kind, branch,
        delivery_offer_id, target_dir, explicit_user_action=False,
    ):
        """Stage one synthetic EPUB; Core checks bytes/SHA/consent and publishes.

        Never allocate a directory, follow a symlink, replace a candidate,
        read a file outside the staging directory, or call a network client.
        """
        with self._lock:
            _, pin = self._bound(account)
            if (type(delivery_offer_id) is not str
                    or _OFFER.fullmatch(delivery_offer_id) is None):
                raise ProviderCapabilityError("reference_offer_invalid")
            selected = self.rights_selected_delivery_offer_snapshot(
                account, edition_id=edition_id, right_id=right_id,
                kind=kind, branch=branch, explicit_user_action=explicit_user_action,
            )
            actual = selected.exact(edition_id, right_id, kind)
            if (actual is None or not actual.delivery_attested
                    or actual.delivery_offer_id != delivery_offer_id
                    or not self._still_bound(account, pin)):
                raise ProviderCapabilityError("reference_offer_unavailable")
            try:
                target = Path(target_dir)
                valid = (
                    target.is_dir() and not target.is_symlink()
                    and target.resolve(strict=True) == target.absolute()
                )
                if not valid:
                    raise ProviderCapabilityError("reference_staging_invalid")
                # Descriptor-relative creation prevents a swapped staging
                # directory from redirecting writes through a new symlink.
                dirfd = os.open(
                    target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                )
            except (TypeError, ValueError, OSError, RuntimeError):
                raise ProviderCapabilityError("reference_staging_invalid") from None
            try:
                parent = os.fstat(dirfd)

                def stage_still_bound():
                    try:
                        current = os.stat(target, follow_symlinks=False)
                        return ((current.st_dev, current.st_ino) ==
                                (parent.st_dev, parent.st_ino))
                    except OSError:
                        return False

                row = self._by_id[edition_id]
                failure_key = (account.identity_id, account.unique_id, edition_id)
                self._delivery_calls += 1
                if row.interrupt_once and failure_key not in self._interrupted:
                    self._interrupted.add(failure_key)
                    raise ProviderCapabilityError("reference_delivery_interrupted")
                hook = self._before_write
                if hook is not None:
                    hook()
                if not self._still_bound(account, pin):
                    raise ProviderCapabilityError("reference_account_rebound")
                if not stage_still_bound():
                    raise ProviderCapabilityError("reference_staging_rebound")
                destination = target / (edition_id + ".epub")
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                try:
                    fd = os.open(destination.name, flags, 0o600, dir_fd=dirfd)
                except OSError:
                    raise ProviderCapabilityError("reference_staging_occupied") from None
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(epub_bytes(row))
                    if not self._still_bound(account, pin):
                        raise ProviderCapabilityError("reference_account_rebound")
                    if not stage_still_bound():
                        raise ProviderCapabilityError("reference_staging_rebound")
                except BaseException:
                    os.unlink(destination.name, dir_fd=dirfd)
                    raise
                return destination, "reference.synthetic.epub"
            finally:
                os.close(dirfd)

    def diagnostic_state(self):
        with self._lock:
            return {
                "provider": self.provider_id, "connected": self.connected,
                "accounts": len(self._accounts), "scenario": self.options.get("scenario", "mixed"),
                "staged_attempts": self._delivery_calls,
            }


class ReferencePlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def activate(self, context):
        self._identity_broker = context.broker("identities")
        return self

    def deactivate(self):
        self._identity_broker = None

    def create_session(
        self, *, credential_id, label, credentials, options, config,
        storage, diagnostics,
    ):
        # Storage is intentionally not retained or passed to the provider.
        return ReferenceSession(
            credential_id=credential_id, label=label,
            credentials=credentials, options=options,
            identity_broker=getattr(self, "_identity_broker", None),
        )


PLUGIN = ReferencePlugin()
