"""Composition of optional maintenance read ports, without UI or activation.

The caller registers these factories at the coordinated page/tool cutover.
Each activation gets independent state and an explicit read-only allowlist.
"""
from .activation_scoped_broker import ActivationScopedReadFactory
from .library_organizer_broker import LibraryOrganizerReadBrokerImpl
from .local_origin_review_broker import LocalOriginBatchReadBrokerImpl
from .derived_maintenance_broker import DerivedMaintenanceReadBrokerImpl


def maintenance_read_factories(service):
    return {
        "library.organizer.read": ActivationScopedReadFactory(
            lambda: LibraryOrganizerReadBrokerImpl(service),
            ("preview", "next_page", "select_all_advice", "cancel"),
        ),
        "library.origin.read": ActivationScopedReadFactory(
            lambda: LocalOriginBatchReadBrokerImpl(service),
            ("preview", "next_page", "cancel"),
        ),
        "library.derived.read": ActivationScopedReadFactory(
            lambda: DerivedMaintenanceReadBrokerImpl(service),
            ("overview", "capacity", "list_receipts", "next_page",
             "verify", "simulate", "cancel"),
        ),
    }
