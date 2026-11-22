"""Generic exact Library-detail review host; no plugin-specific branches.

All worker guards are inert scope coordinates. Actual GTK selection checks,
button creation and rendering stay on the main thread. No work on mounting.
"""
from threading import get_ident

from ..core.source_context import source_context_epoch
from ..plugin_api.guided_book_review import GuidedBookReviewTool
from ..task_queue import TaskPriority
from .guided_maintenance_host import GuidedMaintenanceHostLease
from .local_public_c_detail_guard import local_public_web_selection_check


def _text(value, maximum):
    return type(value) is str and 0 < len(value) <= maximum


class GuidedBookReviewLease:
    __slots__ = ("_lease", "_placement", "_ui_current", "_thread", "_action",
                 "_group", "_toast", "_buttons", "_ids", "_dispose",
                 "_phase", "_pending", "_group_disposers", "_issued", "_retire",
                 "_native_result", "_submission_generation")

    def __init__(self, lease, placement, ui_current, add_action, add_group, toast,
                 retire=None, native_result=None):
        self._lease, self._placement = lease, placement
        self._ui_current, self._thread = ui_current, get_ident()
        self._action, self._group, self._toast = add_action, add_group, toast
        self._retire = retire
        self._native_result = native_result
        self._submission_generation = 0
        self._buttons, self._ids = {}, set()
        self._issued = 0
        self._dispose = None
        self._phase = None
        self._pending = []
        self._group_disposers = []

    @property
    def identity_id(self):
        return self._lease.identity_id

    @property
    def placement_id(self):
        return self._placement

    def is_current(self):
        return self._lease.is_current()

    def _ui_live(self):
        try:
            return (get_ident() == self._thread and self.is_current()
                    and self._ui_current() is True)
        except Exception:
            return False

    def submit(self, label, work, done):
        if (self._phase != "action" or not self._ui_live()
                or not callable(work) or not callable(done)):
            return False
        # At most one submission per explicit mapped click, even when a
        # runner synchronously delivers a result or refuses admission.
        self._phase = "submitted"
        self._submission_generation += 1
        generation = self._submission_generation

        def current():
            return self._submission_generation == generation and self.is_current()

        def deliver(result):
            if not self._ui_live():
                return
            previous = self._phase
            self._phase = "delivery"
            try:
                done(result)
                if callable(self._native_result) and current() and self._ui_live():
                    self._native_result(result, current,
                                        lambda: current() and self._ui_live())
            finally:
                self._phase = previous

        return self._lease.submit(label, work, deliver)

    def add_group(self, title, rows):
        if (self._phase == "delivery" and self._ui_live() and _text(title, 160) and type(rows) is tuple
                and 0 < len(rows) <= 64
                and all(type(row) is tuple and len(row) == 2
                        and _text(row[0], 2048) and _text(row[1], 4096) for row in rows)):
            remove = self._group(title, list(rows))
            if callable(remove):
                if self._ui_live():
                    self._group_disposers.append(remove)
                else:
                    remove()

    def add_action(self, action_id, title, tooltip, invoke):
        if (self._phase not in ("mount", "delivery") or not self._ui_live()
                or not _text(action_id, 80)
                or not _text(title, 256) or not _text(tooltip, 1024)
                or not callable(invoke) or action_id in self._ids or len(self._ids) >= 12
                or self._issued >= 64):
            return
        self._ids.add(action_id)
        self._issued += 1

        def clicked(button):
            if (self._phase is None and self._ui_live()
                    and not getattr(button, "_cifereca_action_context_revoked", False)
                    and button.get_mapped()):
                self._phase = "action"
                try:
                    invoke()
                except Exception:
                    self.toast("Revue indisponible.")
                finally:
                    self._phase = None

        args = (action_id, title, tooltip, clicked)
        if self._phase == "mount":
            self._pending.append(args)
        else:
            self._publish_action(args)

    def _publish_action(self, args):
        button = self._action(*args)
        if not self._ui_live():
            button._cifereca_action_context_revoked = True
            hide = getattr(button, "set_visible", None)
            if callable(hide):
                hide(False)
            return
        self._buttons[args[0]] = button

    def retire_action(self, action_id):
        """Optional bounded replacement of a live action during delivery only."""
        if (self._phase != "delivery" or not self._ui_live()
                or not _text(action_id, 80) or action_id not in self._buttons):
            return False
        button = self._buttons.pop(action_id)
        self._ids.discard(action_id)
        button._cifereca_action_context_revoked = True
        button._cifereca_review_action_retired = True
        hide = getattr(button, "set_visible", None)
        if callable(hide):
            hide(False)
        if callable(self._retire):
            self._retire(button)
        return True

    def _mount(self, contribute):
        if self._phase is not None or self._dispose is not None or not self._ui_live():
            return False
        self._phase = "mount"
        try:
            disposer = contribute(self)
            if callable(disposer):
                self._dispose = disposer
            if not callable(disposer) or not self._ui_live():
                self.dispose()
                return False
            # Contributions are staged until successful mount. A failed
            # plugin cannot leave a live button or launch a preview.
            for args in tuple(self._pending):
                if not self._ui_live():
                    self.dispose()
                    return False
                self._publish_action(args)
            if not self._ui_live():
                self.dispose()
                return False
            return True
        except Exception:
            self.dispose()
            return False
        finally:
            self._pending.clear()
            self._phase = None

    def toast(self, message):
        if (self._phase in ("action", "submitted", "delivery")
                and self._ui_live() and _text(message, 512)):
            self._toast(message)

    def dispose(self):
        self._lease.dispose()
        self._pending.clear()
        buttons, self._buttons = tuple(self._buttons.values()), {}
        groups, self._group_disposers = self._group_disposers, []
        for button in buttons:
            button._cifereca_action_context_revoked = True
        for button in buttons:
            hide = getattr(button, "set_visible", None)
            if callable(hide):
                hide(False)
        for remove in groups:
            try:
                remove()
            except Exception:
                pass
        dispose, self._dispose = self._dispose, None
        if callable(dispose):
            try:
                dispose()
            except Exception:
                pass


def admit_guided_book_review(window, detail, row, item, *, token, resolved):
    """Capture one exact row and activation; no SQL or file hashing on GTK."""
    try:
        service, manager, runner = window.service, window.plugin_manager, window.runner
        owner = service.current_identity
        identity, storage = owner.identity_id, service.storage
        placement, key = item.get("placement_id"), row.key
        route = window._route_display_token
        navigation = window._identity_context_generation
        account = service._account_context_epoch
        epoch = source_context_epoch(service)
        registry, plugin = resolved.registry_generation, resolved.service
        if (type(item) is not dict or not _text(identity, 256)
                or not _text(placement, 256) or not _text(key, 1024)
                or route is None or token is None
                or any(type(x) is not int or x < 0 for x in (navigation, account, registry))
                or resolved.category_id != "maintenance.tool"
                or not _text(resolved.plugin_id, 256)):
            return None

        def exact_row():
            return (window.service is service and service.storage is storage
                    and service.current_identity is owner and owner.identity_id == identity
                    and item.get("identity_id") == identity
                    and item.get("_library_view_owner") in (None, identity)
                    and item.get("placement_id") == placement and item.get("local") is True
                    and item.get("_public_canonical_document_link") is None
                    and item.get("_public_canonical_cover_link") is None
                    and window._library_objects.get(key) is item
                    and key not in getattr(window, "_library_ambiguous_keys", ())
                    and not getattr(row, "_library_ambiguous_key", False))

        def valid():
            return (not getattr(window, "_closed", False)
                    and window.plugin_manager is manager and window.runner is runner
                    and window._route_display_token is route
                    and window._public_web_selection_library is token
                    and type(window._identity_context_generation) is int
                    and window._identity_context_generation == navigation
                    and type(service._account_context_epoch) is int
                    and service._account_context_epoch == account
                    and source_context_epoch(service) == epoch
                    and type(manager._registry_generation) is int
                    and manager._registry_generation == registry
                    and manager.service(resolved.plugin_id) is plugin
                    and exact_row())

        ui_current = local_public_web_selection_check(
            window, row, view="library", token=token, source_current=exact_row)

        def enqueue(work, finish):
            runner.submit("Revue documentaire locale…", work, finish,
                          lambda _exc: finish(None), priority=TaskPriority.USER_ACTION,
                          kind="detail-cache", origin="user")

        def add_action(action_id, title, tooltip, callback):
            button = window.action_button(title, "system-search-symbolic")
            button._cifereca_action_id = "book-review:" + resolved.plugin_id + ":" + action_id
            button._cifereca_action_priority = 35
            button.set_tooltip_text(tooltip)
            button.connect("clicked", callback)
            detail.add_actions([button])
            return button

        def native_result(value, current, visible):
            # Ordinary review data cannot trigger a native confirmation.
            # Resolve only the exact private preview already owned by Core.
            from ..core.local_origin_detail_broker import take_native_local_origin_declaration
            pending = take_native_local_origin_declaration(
                manager, resolved, value, service=service,
                identity_id=identity, placement_id=placement)
            if pending is None or not visible():
                return
            from gi.repository import Adw
            from .library_declaration_actions import show_private_origin_declaration
            preview, preview_current = pending
            show_private_origin_declaration(
                Adw=Adw, window=window, runner=runner, service=service, preview=preview,
                current=lambda: current() and preview_current(),
                visible=lambda: visible() and preview_current())

        lease = GuidedBookReviewLease(
            GuidedMaintenanceHostLease(identity, valid, enqueue), placement,
            ui_current, add_action, detail.add_review_group, window.toast,
            retire=getattr(detail, "remove_review_action", None),
            native_result=native_result)
        return lease if lease._ui_live() else None
    except Exception:
        return None


def mount_guided_book_reviews(window, detail, row, item, *, token):
    """Discover only live contributions. Never fall back to a native tool."""
    try:
        services = tuple(window.plugin_manager.services("maintenance.tool"))
    except Exception:
        return ()
    mounted = []
    for resolved in services:
        if not isinstance(resolved.service, GuidedBookReviewTool):
            continue
        lease = admit_guided_book_review(window, detail, row, item,
                                         token=token, resolved=resolved)
        if lease is None:
            continue
        if lease._mount(resolved.service.contribute_book_review):
            mounted.append(lease)
    return tuple(mounted)


def dispose_guided_book_reviews(window):
    """Retire exact former mounts before any reentrant GTK disposal signal."""
    mounts = getattr(window, "_guided_book_review_mounts", ())
    window._guided_book_review_mounts = ()
    for lease in mounts:
        lease.dispose()
