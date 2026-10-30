"""Numilog direct owned-edition acquisition, extracted from LibraryService.

Behaviour-preserving seam for #1120. Every provider call remains under the
injected global cross-process acquisition lock; exact account/identity and
artifact re-admissions are unchanged. Do not call from catalogue or UI render.

Caller supplies historical LibraryService dependency seams explicitly to keep
existing services-level monkeypatches and lock instrumentation effective.
"""
from __future__ import annotations

from typing import Any


def acquire_numilog_direct(
    service: Any, item: Any, *, _admission: Any, session: Any,
    InterProcessLock: Any, acquisition_lock_file: Any,
    LockBusyError: type[Exception], ProviderConfigurationError: type[Exception],
    hashlib: Any, reconcile_pending: bool = False,
) -> Any:
    # Even an explicit UI click is not permission to download from a
    # detached account or to race an existing manual/materialize run.
    # Numilog resolves delivery URLs afresh *inside* download_direct.
    from .numilog_manual_acquisition import require_source
    from .numilog_startup_diagnostics import acquisition_refusal
    from ..plugin_api import Cancelled
    # Only the *first* failed boundary is recorded by the opt-in startup
    # trace. These closed values never expose the error's private contents.
    stage = "acquisition_source_unavailable"
    try:
        with InterProcessLock(acquisition_lock_file()):
            identity, pinned_session, pinned_account_id = require_source(
                service, item, session=session,
            )
            # The caller's exact immutable purchase target is checked
            # before I/O and again before/after hooks. A refreshed item
            # cannot switch credential, purchase, variant or revision.
            from .numilog_manual_acquisition import admission_scope, require_admission
            pinned_scope = admission_scope(item)

            def readmit():
                return require_admission(
                    service, item, _admission, pinned_scope=pinned_scope,
                    session=pinned_session, identity_id=identity.identity_id,
                    account_id=pinned_account_id,
                )

            stage = "acquisition_local_conflict"
            if reconcile_pending:
                from .numilog_recovery import recovery_gate
                from .numilog_materialization_consent import mode
                from ..brokers import SynchronizationBrokerImpl
                recovery_broker = SynchronizationBrokerImpl(service)
                matching = [account for account in service.accounts
                    if account.provider_id == 'numilog'
                    and account.credential_id == item.source_credential
                    and account.identity_id == identity.identity_id
                    and account.unique_id == pinned_account_id]
                recovery_source_id = (
                    recovery_broker._source_id(matching[0]) if len(matching) == 1
                    else None
                )
                recovery_epoch = (
                    service.storage.numilog_materialization_consent_revision(recovery_source_id)
                    if recovery_source_id is not None else None
                )
                def recovery_authorized():
                    # An internal recovery flag alone is never permission to
                    # download; the exact persisted opt-in must still exist.
                    return bool(
                        recovery_epoch is not None
                        and mode(recovery_broker, recovery_source_id) == 'on_start'
                        and service.storage.numilog_materialization_consent_revision(
                            recovery_source_id,
                        ) == recovery_epoch
                        and recovery_gate(
                            service.storage, identity_id=identity.identity_id,
                            credential_id=item.source_credential,
                            account_id=pinned_account_id, item_id=item.record_id,
                        ) == 'recoverable'
                    )
                if (_admission is None or item.owned is not True
                        or item.downloadable is not True
                        or not recovery_authorized()):
                    raise ProviderConfigurationError('Numilog : reprise non attestée')
            from .library_declared_links import has_active_declared_link
            if has_active_declared_link(
                service.storage, identity_id=identity.identity_id,
                provider_id='numilog', credential_id=item.source_credential,
                account_id=pinned_account_id, item_id=item.record_id,
            ):
                raise ProviderConfigurationError(
                    "Numilog : association locale déclarée ; vérifier le fichier avant de télécharger"
                )
            if not reconcile_pending and service.storage.numilog_purchase_has_local_conflict(
                credential_id=item.source_credential,
                item_id=item.record_id,
            ):
                raise ProviderConfigurationError(
                    "Numilog : acquisition existante ; rapprochement local requis"
                )
            stage = "acquisition_root_unavailable"
            target_dir = service.library_identity_provider_dir(
                identity.identity_id, "numilog",
            )
            if (target_dir.is_symlink()
                    or target_dir.parent.is_symlink()
                    or not target_dir.is_dir()):
                raise ProviderConfigurationError(
                    "Numilog : répertoire identité non attesté"
                )
            # Numilog's explicit direct adapter uses a stable hashed
            # filename. A local orphan, broken symlink or pre-existing
            # file with no ledger is still a conflict, never an
            # invitation to overwrite it on another manual click.
            expected_name = "numilog-" + hashlib.sha256(
                item.record_id.encode("utf-8"),
            ).hexdigest()[:32] + ".epub"
            expected_path = target_dir / expected_name
            stage = "acquisition_file_conflict"
            provider_target = target_dir
            if expected_path.exists() or expected_path.is_symlink():
                if not reconcile_pending:
                    raise ProviderConfigurationError(
                        "Numilog : fichier local candidat ; rapprochement requis"
                    )
                # The hashed canonical filename belongs to some pre-existing
                # publication. Leave it untouched and download in an isolated,
                # bounded location under this SAME identity/provider root.
                provider_target = target_dir / ("recovery-" + hashlib.sha256(
                    item.record_id.encode("utf-8"),
                ).hexdigest()[:32])
                if provider_target.exists() or provider_target.is_symlink():
                    raise ProviderConfigurationError(
                        "Numilog : reprise déjà matérialisée à vérifier"
                    )
            from .numilog_publication_guard import require_tracked_publications
            stage = "acquisition_publication_guard"
            if not reconcile_pending:
                require_tracked_publications(service, identity.identity_id, target_dir)
            # A recovery cannot claim any historical file by name or by SHA.
            # A fresh provider GET is permitted ONLY into its otherwise empty
            # exact destination; no existing byte is overwritten or removed.
            # Recheck the exact caller/source after bounded filesystem
            # inspection and immediately before any provider I/O.
            stage = "acquisition_admission_failed"
            item = readmit()
            if reconcile_pending and not recovery_authorized():
                raise ProviderConfigurationError('Numilog : preuves modifiées pendant la reprise')
            stage = "acquisition_delivery_failed"
            try:
                path, _private_url = pinned_session.download_direct(
                    item, provider_target,
                )
            except Exception as exc:
                from ..plugin_api import Cancelled
                if isinstance(exc, Cancelled):
                    raise
                # A provider exception can contain a signed ACSM link
                # or order/cookie data. Never relay its raw message.
                raise ProviderConfigurationError(
                    "Numilog : livraison indisponible ; réessayer manuellement"
                ) from None
            # The provider may release its per-session lock before
            # returning. A reconnect/rebind or revoked recovery opt-in during
            # HTTP/ADEPT cannot be committed under a different identity.
            stage = "acquisition_admission_failed"
            if reconcile_pending and not recovery_authorized():
                raise ProviderConfigurationError('Numilog : consentement de reprise révoqué')
            readmit()
            refreshed = require_source(service, item, session=pinned_session)
            if (refreshed[0].identity_id != identity.identity_id
                    or refreshed[2] != pinned_account_id):
                raise ProviderConfigurationError(
                    "Numilog : rattachement du compte modifié"
                )
            from .numilog_manual_acquisition import require_local_artifact
            stage = "acquisition_artifact_invalid"
            require_local_artifact(path, target_dir)
            from dataclasses import replace
            # Never pass unverified private cover/link metadata to
            # post-download hooks or the persisted Acquisition object.
            # The original item is used only by the provider's
            # exact owned-library resolver, inside the source lock.
            clean_item = replace(
                item, thumbnail_url=None, friendly_url=None,
                download_url=None, metadata={},
            )
            stage = "acquisition_post_delivery_failed"
            service.acquisition.run_post_download(
                clean_item, path, "", target_dir,
            )
            def final_admission():
                readmit()
                refreshed = require_source(service, item, session=pinned_session)
                if (refreshed[0].identity_id != identity.identity_id
                        or refreshed[2] != pinned_account_id):
                    raise ProviderConfigurationError(
                        "Numilog : rattachement du compte modifié"
                    )
            # Post-download hooks may remove or replace the file.
            # Attest the final artifact, not just its pre-hook path.
            stage = "acquisition_artifact_invalid"
            require_local_artifact(path, target_dir)
            from .numilog_materialization_checkpoint import record
            stage = "acquisition_receipt_failed"
            checkpoint_id = record(
                service, item, path, target_dir, identity.identity_id, pinned_account_id,
                validate=final_admission, recovery=reconcile_pending,
            )
            # A provider-returned URL may contain single-use ACSM
            # signatures. Never persist it as Acquisition.source_url.
            result = service.acquisition.acquisition_from_direct(
                clean_item, path=path, url="", target_dir=target_dir,
                action="download",
            )
            # The generic direct adapter historically dropped the
            # account ID. Persist the source's originally pinned owner,
            # not a later enumeration of possibly mutated accounts.
            return replace(
                result, account_id=pinned_account_id,
                metadata={**result.metadata,
                          "identity_id": identity.identity_id,
                          "numilog_checkpoint_id": checkpoint_id},
            )
    except LockBusyError:
        acquisition_refusal("acquisition_lock_busy")
        raise ProviderConfigurationError(
            "Numilog : acquisition locale déjà en cours"
        ) from None
    except Cancelled:
        raise
    except Exception:
        acquisition_refusal(stage)
        raise
