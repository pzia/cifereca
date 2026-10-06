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
    ) -> None:
        self._acquire = acquire
        self._index_path = index_path
        self._resolve_identity = resolve_identity

    def obtain(self, item: SearchResult) -> MaterializationResult:
        acquisition = self._acquire(item)
        path = acquisition.path
        if path is None or not path.is_file():
            return MaterializationResult(acquisition, None, None, None)
        identity = self._resolve_identity(item.source_provider, item.source_credential)
        indexed = self._index_path(
            path,
            title=item.title,
            item_id=item.record_id,
            identity_id=identity.identity_id,
            provider_id=item.source_provider,
        )
        if indexed.get("status") != "indexed":
            raise ProviderConfigurationError(
                f"Indexation locale impossible après acquisition: {indexed.get('reason') or indexed.get('status')}"
            )
        return MaterializationResult(
            acquisition=acquisition,
            indexed=indexed,
            artifact_id=str(indexed.get("artifact_id") or "") or None,
            identity_id=identity.identity_id,
        )
