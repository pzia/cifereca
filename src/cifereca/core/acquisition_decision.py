from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


AcquisitionAction = Literal['reuse', 'acquire', 'repair_legacy', 'reacquire_explicit']


@dataclass(frozen=True, slots=True)
class AcquisitionDecision:
    action: AcquisitionAction
    reason: str
    source: str = ''

    def as_dict(self) -> dict[str, str]:
        return {'action': self.action, 'reason': self.reason, 'source': self.source}


def decide_artifact_reuse(
    *,
    force_refresh: bool,
    catalog_valid: bool,
    ledger_complete_valid: bool,
    legacy_repair: bool,
    legacy_reason: str = '',
    previous_exists: bool = False,
    previous_path_exists: bool = False,
    previous_valid: bool | None = None,
    previous_protected: bool | None = None,
) -> AcquisitionDecision:
    """Describe the existing acquisition policy without causing I/O."""
    if force_refresh:
        return AcquisitionDecision('reacquire_explicit', 'explicit_force_refresh', 'user')
    if ledger_complete_valid:
        return AcquisitionDecision('reuse', 'valid_ledger_artifact', 'ledger')
    if catalog_valid and not legacy_repair:
        return AcquisitionDecision('reuse', 'valid_catalog_artifact', 'catalog')
    if legacy_repair:
        return AcquisitionDecision(
            'repair_legacy',
            legacy_reason or 'legacy_artifact_requires_repair',
            'ledger',
        )
    if previous_exists:
        if not previous_path_exists:
            return AcquisitionDecision('acquire', 'ledger_artifact_missing', 'ledger')
        if previous_protected is True:
            return AcquisitionDecision('acquire', 'ledger_artifact_protected', 'ledger')
        if previous_valid is False:
            return AcquisitionDecision('acquire', 'ledger_artifact_invalid', 'ledger')
        return AcquisitionDecision('acquire', 'ledger_artifact_unusable', 'ledger')
    return AcquisitionDecision('acquire', 'no_local_artifact', 'none')


__all__ = ['AcquisitionDecision', 'AcquisitionAction', 'decide_artifact_reuse']
