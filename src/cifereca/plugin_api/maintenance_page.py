"""#7100 additive UI port for the optional Maintenance page.

The page owns its layout and lifecycle. The host owns contribution discovery,
public generic tool widgets, confirmations, worker admission and disposal.
No raw registry, MainWindow, Storage, tool plan or execution authority crosses
this port. An exact active PageViewContext is required at each mount.
"""
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from .models import UiPageHost
from .page_lifecycle import PageViewContext


@dataclass(frozen=True, slots=True)
class MaintenancePageContent:
    widget: object
    dispose: Callable[[], None]


@runtime_checkable
class UiMaintenancePageHost(UiPageHost, Protocol):
    def mount_maintenance_tools(self, context: PageViewContext) -> MaintenancePageContent | None:
        """UI owner only. No worker is admitted by mounting the tool list."""
        ...
