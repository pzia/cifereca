"""CoreUI owner of the optional maintenance page's generic content.

The page receives only a widget and a disposer. No business work is admitted
by mounting. Legacy rows retain their existing controls and confirmation;
guided contributions are admitted using the same runner and exact view lease.
"""
from ..plugin_api.page_lifecycle import PageViewContext
from ..plugin_api.guided_maintenance import GuidedMaintenanceTool, GuidedMaintenanceView
from .guided_maintenance_host import admit_guided_maintenance_host


def mount_page_content(window, context, *, Gtk):
    if (type(context) is not PageViewContext or context.route != "tools"
            or not context.is_current() or getattr(window, "_closed", False)):
        return None
    identity = getattr(getattr(window.service, "current_identity", None), "identity_id", None)
    if context.identity_id != identity:
        return None
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    # The renderer's existing row-disposal protocol is retained. Only the
    # current owner can dispose the new content; a late old dispose is inert.
    window._dispose_maintenance_lines()
    if not context.is_current() or getattr(window, "_closed", False):
        return None
    owner = object()
    window._maintenance_mount_owner = owner
    window._tools_box = box

    def dispose():
        if getattr(window, "_maintenance_mount_owner", None) is not owner:
            return
        window._maintenance_mount_owner = None
        window._tools_box = None
        window._dispose_maintenance_lines()

    try:
        window._refresh_tools_page(_page_context=context)
        if (not context.is_current() or getattr(window, "_closed", False)
                or getattr(window, "_maintenance_mount_owner", None) is not owner):
            dispose()
            return None
        return box, dispose
    except Exception:
        dispose()
        return None


def mount_guided_tool(window, box, resolved, *, Gtk):
    """Return True for guided tools, even on failure: never fallback to run()."""
    tool = resolved.service
    if not isinstance(tool, GuidedMaintenanceTool):
        return False
    lease = admit_guided_maintenance_host(window, resolved)
    view = None
    try:
        if lease is None:
            raise ValueError("guided_lease_unavailable")
        view = tool.build_guided(lease)
        if (type(view) is not GuidedMaintenanceView or not isinstance(view.widget, Gtk.Widget)
                or not callable(view.dispose) or not lease.is_current()):
            raise ValueError("guided_view_unavailable")
        box.append(view.widget)

        def dispose():
            lease.dispose()
            try:
                view.dispose()
            except Exception:
                pass

        window._guided_maintenance_disposers.append(dispose)
    except Exception:
        if lease is not None:
            lease.dispose()
        if type(view) is GuidedMaintenanceView and callable(view.dispose):
            try:
                view.dispose()
            except Exception:
                pass
        box.append(Gtk.Label(label="Outil de maintenance guidé indisponible.", xalign=0))
    return True
