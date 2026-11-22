"""#7087 CoreUI-owned generic worker lease for guided maintenance contributions.

No GTK or concrete plugin import. A view owns this adapter until disposal;
its public surface is identity_id, is_current and submit. The future registry
mount adapter must dispose on detach, rebuild and shutdown. No producer
exception or arbitrary plugin task label reaches the runner's diagnostics.
"""
from threading import Lock

from ..core.source_context import source_context_epoch
from ..task_queue import TaskPriority


class GuidedMaintenanceHostLease:
    __slots__ = ("_identity_id", "_valid", "_enqueue", "_lock", "_disposed", "_ticket", "_observer")

    def __init__(self, identity_id, valid, enqueue):
        self._identity_id = identity_id
        self._valid = valid
        self._enqueue = enqueue
        self._lock = Lock()
        self._disposed = False
        self._ticket = None
        self._observer = None  # private native mount observer, not a plugin port

    @property
    def identity_id(self):
        return self._identity_id

    def is_current(self):
        with self._lock:
            if self._disposed:
                return False
        try:
            return self._valid() is True
        except Exception:
            return False

    def submit(self, label, work, done):
        if not callable(work) or not callable(done) or not self.is_current():
            return False
        with self._lock:
            if self._disposed or self._ticket is not None:
                return False
            ticket = self._ticket = object()
        if self._observer is not None:
            self._observer(None)

        def current():
            with self._lock:
                admitted = self._ticket is ticket and not self._disposed
            return admitted and self.is_current()

        def guarded_work():
            if not current():
                return None
            value = work()
            return value if current() else None

        def finish(value):
            with self._lock:
                if self._disposed or self._ticket is not ticket:
                    return
                self._ticket = None
            if self.is_current():
                try:
                    done(value)
                    with self._lock:
                        idle = self._ticket is None and not self._disposed
                    if idle and self.is_current() and self._observer is not None:
                        self._observer(value)
                except Exception:
                    # Do not expose private plugin error text to the UI/log.
                    pass

        try:
            # Display and logs use a fixed Core-owned label. Worker execution
            # stays in the shared task runner, never a plugin-created thread.
            self._enqueue(guarded_work, finish)
            return True
        except Exception:
            with self._lock:
                if self._ticket is ticket:
                    self._ticket = None
            return False

    def dispose(self):
        with self._lock:
            self._disposed = True
            self._ticket = None


def admit_guided_maintenance_host(window, resolved, *, view_current=None):
    """Capture one mounted view; construction performs no work.

    Only inert scope/registry coordinates are checked on workers. GTK
    attachment, navigation and cancellation are the mount owner's job.
    """
    try:
        service, manager, runner = window.service, window.plugin_manager, window.runner
        owner = service.current_identity
        identity = owner.identity_id
        navigation = window._identity_context_generation
        route = window._route_display_token
        epoch = source_context_epoch(service)
        registry = resolved.registry_generation
        plugin_id, plugin = resolved.plugin_id, resolved.service
        if (type(identity) is not str or not identity
                or (route is None and view_current is None)
                or (view_current is not None and not callable(view_current))
                or type(navigation) is not int or type(registry) is not int
                or resolved.category_id != "maintenance.tool"):
            return None

        def valid():
            return (
                not getattr(window, "_closed", False)
                and window.service is service and window.plugin_manager is manager
                and window.runner is runner
                and service.current_identity is owner and owner.identity_id == identity
                and window._identity_context_generation == navigation
                and (view_current() is True if view_current is not None
                     else window._route_display_token is route)
                and source_context_epoch(service) == epoch
                and manager._registry_generation == registry
                and manager.service(plugin_id) is plugin
            )

        def enqueue(work, finish):
            runner.submit(
                "Revue locale de maintenance…", work, finish, lambda _exc: finish(None),
                priority=TaskPriority.MAINTENANCE, kind="maintenance-plan", origin="user",
            )

        lease = GuidedMaintenanceHostLease(identity, valid, enqueue)
        return lease if lease.is_current() else None
    except Exception:
        return None
