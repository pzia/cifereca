from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..acquisition import Acquisition
from ..models import SearchResult
from ..providers import ProviderConfigurationError


@dataclass(frozen=True, slots=True)
class MaterializationResult:
    acquisition: Acquisition
    indexed: dict[str, Any] | None
    artifact_id: str | None
    identity_id: str | None

    @property
    def path(self) -> Path | None:
        return self.acquisition.path


class MaterializationCoordinator:
    """Turns a provider acquisition into cifereca-owned durable materialization."""

    def __init__(
        self,
        *,
        acquire: Callable[[SearchResult], Acquisition],
        index_path: Callable[..., dict[str, Any]],
        resolve_identity: Callable[[str, str], Any],
        source_epoch: Callable[[], int] | None = None,
    ) -> None:
        self._acquire = acquire
        self._index_path = index_path
        self._resolve_identity = resolve_identity
        self._source_epoch = source_epoch

    def obtain(self, item: SearchResult) -> MaterializationResult:
        # Never choose the publication owner after supplier I/O. The old
        # source can be rebound while a direct download or override runs.
        epoch = self._source_epoch() if self._source_epoch is not None else None
        coords = (item.source_provider, item.source_credential, item.record_id)
        observed = (item.id, item.source_account_id)
        title = item.title
        identity = self._resolve_identity(*coords[:2])
        owner = getattr(identity, "identity_id", None)
        if type(owner) is not str or not owner:
            raise ProviderConfigurationError("materialization_owner_unproven")

        def still_current():
            try:
                return (
                    (item.source_provider, item.source_credential, item.record_id) == coords
                    and (item.id, item.source_account_id) == observed
                    and (self._source_epoch is None or self._source_epoch() == epoch)
                    and self._resolve_identity(*coords[:2]).identity_id == owner
                    and (self._source_epoch is None or self._source_epoch() == epoch)
                )
            except Exception:
                return False

        if not still_current():
            raise ProviderConfigurationError("materialization_source_revoked")
        acquisition = self._acquire(item)
        if (not still_current()
                or (acquisition.provider_id, acquisition.credential_id, acquisition.item_id)
                != (coords[0], coords[1], str(coords[2] or observed[0]))):
            # Bytes or an override's side effects may already exist. Do not
            # falsely publish them under the replacement borrower.
            raise ProviderConfigurationError("materialization_source_revoked")
        path = acquisition.path
        if path is None or not path.is_file():
            return MaterializationResult(acquisition, None, None, None)
        if not still_current():
            raise ProviderConfigurationError("materialization_source_revoked")
        indexed = self._index_path(
            path,
            title=title,
            item_id=coords[2],
            identity_id=owner,
            provider_id=coords[0],
        )
        if indexed.get("status") != "indexed":
            raise ProviderConfigurationError(
                f"Indexation locale impossible après acquisition: {indexed.get('reason') or indexed.get('status')}"
            )
        return MaterializationResult(
            acquisition=acquisition,
            indexed=indexed,
            artifact_id=str(indexed.get("artifact_id") or "") or None,
            identity_id=owner,
        )
