"""#7100: optional maintenance ports are private composition, not a registry.

The existing PluginManager resolves these factories per activation. No
package imports, GTK, provider calls, or workers on construction. The
desktop runtime will register them with the page/tool cutover.
"""
from .activation_scoped_broker import ActivationScopedReadFactory
from .library_organizer_broker import LibraryOrganizerReadBrokerImpl
from .local_origin_review_broker import LocalOriginBatchReadBrokerImpl
from .local_origin_detail_broker import LocalOriginDetailReadFactory
from .derived_maintenance_broker import DerivedMaintenanceReadBrokerImpl


def maintenance_read_ports(service):
    return {
        "library.organizer.read": ActivationScopedReadFactory(
            lambda: LibraryOrganizerReadBrokerImpl(service),
            ("preview", "next_page", "select_all_advice", "prepare_dry_run", "confirm_dry_run", "revise_dry_run", "cancel"),
        ),
        "library.origin.read": ActivationScopedReadFactory(
            lambda: LocalOriginBatchReadBrokerImpl(service),
            ("preview", "next_page", "cancel"),
        ),
        "library.origin.detail.read": LocalOriginDetailReadFactory(service),
        "library.derived.read": ActivationScopedReadFactory(
            lambda: DerivedMaintenanceReadBrokerImpl(service),
            ("overview", "capacity", "list_receipts", "next_page", "verify", "simulate", "cancel"),
            refusals={"simulate": "review_required"},
        ),
    }
