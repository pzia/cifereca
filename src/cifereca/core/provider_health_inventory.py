"""Read installed provider registry without activation or provider I/O."""
from dataclasses import dataclass


@dataclass(frozen=True)
class InstalledHealth:
    plugin_id: str
    enabled: bool
    state: str
    observations: tuple


def installed_health(manager, health):
    # Registry lifecycle lock is released before acquiring projection lock.
    with manager.hold_descriptive_lifecycle():
        statuses = tuple(manager.statuses())
    observed = health.inventory()
    return tuple(
        InstalledHealth(status.descriptor.id, status.enabled, status.state,
                        tuple(row for row in observed if row[0].provider == status.descriptor.id))
        for status in statuses
        if status.descriptor.type in ("provider.library", "provider.collection")
    )
