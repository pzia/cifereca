"""P0 #4858 D3: one selected Kobo edition through the existing Core A->B executor.

Unlike the old direct SearchResult/ADEPT path, this command never interprets
backup_items, local titles, a catalogue `owned` flag or an arbitrary file SHA
as right, offer or delivery evidence. It routes exactly one attested supplier
right through Core's existing selected-offer and B publication pipeline.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from ..locking import InterProcessLock, LockBusyError
from ..models import Account
from ..paths import acquisition_lock_file
from ..plugin_api.provider_rights_observation import ProviderRightsScope
from .canonical_delivery_abi import has_complete_delivery_abi
from .source_context import source_context_epoch
from .source_sync_consent import read_policy


class SelectedKoboRejected(ValueError):
    """Closed reason only: never include private supplier payload or URL."""

    _KNOWN = frozenset({
        "invalid_selected_request", "selected_source_ambiguous",
        "selected_source_unavailable", "selected_source_disabled",
        "selected_source_changed", "canonical_source_consent_required",
        "selected_edition_unavailable", "selected_edition_ambiguous",
        "selected_acquisition_busy", "selected_delivery_unavailable",
    })

    def __init__(self, code: str):
        if code not in self._KNOWN:
            code = "selected_delivery_unavailable"
        self.code = code
        super().__init__(code)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise SelectedKoboRejected(reason)


def _valid_id(value: object) -> bool:
    return (
        type(value) is str and 0 < len(value) <= 256
        and value == value.strip()
        and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def synchronize_selected_kobo_edition(
    service: Any, *, credential_id: str, edition_id: str,
    explicit_user_action: bool, cancelled: Callable[[], bool] = lambda: False,
) -> dict[str, object]:
    """Execute only ONE exact provider A right under Core's existing B engine.

    The source-wide Core consent must already be ON at the current generation.
    A right's identity (not an untrusted catalogue title or ISBN) selects its
    entry; zero or multiple rights for the same edition fail closed. A complete
    modern provider ABI is mandatory. No second provider batch or legacy GET.
    The result contains closed counters only, no borrower, title, path or URL.
    """
    _require(
        _valid_id(credential_id) and credential_id != "__public__"
        and _valid_id(edition_id)
        and explicit_user_action is True and callable(cancelled),
        "invalid_selected_request",
    )
    try:
        with InterProcessLock(acquisition_lock_file()) as lease:
            accounts = service.accounts
            sessions = service._sessions
            storage = service.storage
            rows = [
                row for row in accounts
                if type(row) is Account and row.provider_id == "kobo"
                and row.credential_id == credential_id
            ]
            _require(
                len(rows) == 1,
                "selected_source_ambiguous" if rows else "selected_source_unavailable",
            )
            account = rows[0]
            candidates = [
                session for session in sessions.values()
                if getattr(session, "provider_id", None) == "kobo"
                and getattr(session, "credential_id", None) == credential_id
            ]
            _require(len(candidates) == 1, "selected_source_unavailable")
            session = candidates[0]
            _require(
                getattr(session, "connected", None) is True
                and has_complete_delivery_abi(session),
                "selected_source_unavailable",
            )
            epoch = source_context_epoch(service)
            session_epoch = getattr(session, "_epoch", None)
            scope = ProviderRightsScope(
                account.identity_id, "kobo", credential_id, account.unique_id,
            )
            policy = read_policy(storage, scope)
            _require(
                policy.mode == "on" and type(policy.revision) is str,
                "canonical_source_consent_required",
            )
            source_revision = policy.revision

            def current() -> bool:
                try:
                    if (source_context_epoch(service) != epoch
                            or service.accounts is not accounts
                            or service._sessions is not sessions
                            or service.storage is not storage
                            or getattr(session, "_epoch", None) != session_epoch
                            or session.connected is not True
                            or service._sessions.get(
                                service._session_key("kobo", credential_id),
                            ) is not session
                            or len([
                                row for row in service.accounts
                                if type(row) is Account
                                and row.provider_id == "kobo"
                                and row.credential_id == credential_id
                            ]) != 1
                            or len(session.accounts) != 1
                            or (
                                session.accounts[0].identity_id,
                                session.accounts[0].provider_id,
                                session.accounts[0].credential_id,
                                session.accounts[0].unique_id,
                            ) != (
                                scope.identity_id, scope.provider_id,
                                scope.credential_id, scope.account_id,
                            )):
                        return False
                    return bool(
                        service.sync_source_enabled(account) is True
                        and storage.provider_scope_account_bound(
                            identity_id=scope.identity_id,
                            provider_id=scope.provider_id,
                            credential_id=scope.credential_id,
                            account_id=scope.account_id,
                        ) is True
                        and read_policy(storage, scope).mode == "on"
                        and read_policy(storage, scope).revision == source_revision
                    )
                except Exception:
                    return False

            _require(current(), "selected_source_disabled")
            _require(cancelled() is False, "selected_source_changed")
            from .rights_snapshot_read import read_account_rights_snapshot
            from .canonical_sync_stage import persist_attested_source_references
            from .sync_coordination import _run_observed_canonical_convergence

            snapshot = read_account_rights_snapshot(
                service, account, cancelled=cancelled,
            )
            _require(current(), "selected_source_changed")
            _require(
                snapshot.scope == scope
                and snapshot.status in {"partial", "success"}
                and bool(snapshot.branch),
                "selected_source_unavailable",
            )
            # One edition may legally have multiple supplier rights or source
            # branches. Never turn an edition-only action into multiple GETs.
            matches = [row for row in snapshot.rights
                       if row.edition_id == edition_id
                       and row.provider_attested]
            _require(
                len(matches) == 1,
                "selected_edition_ambiguous" if len(matches) > 1
                else "selected_edition_unavailable",
            )
            narrowed = replace(
                snapshot, rights=(matches[0],), status="partial",
                coverage="partial", pagination_complete=False,
                resume_cursor="",
            )
            _require(current(), "selected_source_changed")
            persist_attested_source_references(
                storage, narrowed, context_current=current,
            )
            _require(current(), "selected_source_changed")
            counts = _run_observed_canonical_convergence(
                service, account, [narrowed],
                admitted_epoch=epoch, sync_lease=lease,
                cancelled_for_rights=cancelled,
            )
            _require(current(), "selected_source_changed")
            expected = (
                "downloaded", "recovered", "present_attested", "pending",
                "blocked", "failed", "cancelled", "skipped_archived", "inactive",
            )
            _require(
                type(counts) is dict and set(counts) == set(expected)
                and all(type(value) is int and 0 <= value <= 1
                        for value in counts.values())
                and sum(counts.values()) == 1,
                "selected_delivery_unavailable",
            )
            outcome = next(name for name in expected if counts[name])
            return {
                "schema": "core.kobo.selected-canonical/1",
                "status": outcome,
                "ok": outcome in {"downloaded", "recovered", "present_attested"},
                "changed": int(outcome == "downloaded"),
                "counts": {name: counts[name] for name in expected},
                "canonical_only": True, "legacy_backup_mutations": 0,
            }
    except LockBusyError:
        raise SelectedKoboRejected("selected_acquisition_busy") from None
    except SelectedKoboRejected:
        raise
    except Exception:
        # The original provider error may contain credentials, absolute
        # paths, signed URLs or personal-library metadata. Never reflect it.
        raise SelectedKoboRejected("selected_delivery_unavailable") from None


__all__ = ["SelectedKoboRejected", "synchronize_selected_kobo_edition"]
