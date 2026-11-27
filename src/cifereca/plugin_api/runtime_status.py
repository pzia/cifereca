"""Optional descriptive runtime status port, independent of vendor wire schemas.

This snapshot never grants rights, authorizes a transfer, or selects a borrower.
The installed runtime owns translation of its external protocol. The Core owns
consumer validation, cache lifetime and all acquisition/admission decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

RUNTIME_STATUS_CONTRACT = "cifereca.runtime.status/1"


@dataclass(frozen=True, slots=True)
class RuntimeStatusSnapshot:
    available: bool
    compatible: bool = False
    operational: bool = False
    download_available: bool = False
    acsm_available: bool = False
    delegated_available: bool = False
    pinned_delivery_candidate: bool = False
    separate_content_keys_replay: bool = False
    protected_content_available: bool = False
    formats: tuple[str, ...] = ()
    contract_version: int | None = None
    minimum_supported_contract: int = 0
    details: dict[str, object] = field(default_factory=dict)
    schema: str = RUNTIME_STATUS_CONTRACT


@runtime_checkable
class RuntimeStatusReader(Protocol):
    def runtime_status(self, *, full: bool = False) -> RuntimeStatusSnapshot: ...


__all__ = ["RUNTIME_STATUS_CONTRACT", "RuntimeStatusSnapshot", "RuntimeStatusReader"]
