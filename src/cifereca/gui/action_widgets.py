"""Consistent buttons and adaptive action bars for built-in pages."""
from gi.repository import Adw, Gtk
from .action_layout import action_budget, compact_actions


def action_button(label, icon_name=None, *, suggested=False, destructive=False, tooltip=None):
    button = Gtk.Button()
    button._cifereca_action_label = label
    button._cifereca_action_icon = icon_name or 'view-more-symbolic'
    if icon_name:
        try:
            content = Adw.ButtonContent()
            content.set_label(label); content.set_icon_name(icon_name)
            button.set_child(content)
        except (AttributeError, TypeError):
            button.set_label(label)
    else:
        button.set_label(label)
    if suggested: button.add_css_class('suggested-action')
    if destructive: button.add_css_class('destructive-action')
    button.set_tooltip_text(tooltip or label)
    return button


class AdaptiveActionBar(Gtk.ScrolledWindow):
    """Half-page budget; keep labelled actions, compact on overflow.

    The last fallback is horizontal scrolling when even icons do not fit.
    Buttons and their callbacks stay the same objects through every resize.
    """
    def __init__(self, *, fraction=0.5, source=None):
        super().__init__()
        self.fraction = fraction; self.source = source
        self._buttons = []; self._widths = {}; self._compact = None; self._budget = None
        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.box.add_css_class('toolbar')
        self.box.set_halign(Gtk.Align.END)
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.set_propagate_natural_width(False)
        self.set_min_content_width(0)
        self.set_valign(Gtk.Align.START)
        self.set_child(self.box)
        self.add_tick_callback(self._resize)

    def append(self, button):
        if isinstance(button, Gtk.Button) and not getattr(button, '_cifereca_action_label', None):
            icon = button.get_icon_name()
            caption = button.get_label()
            labels = {'view-refresh-symbolic':'Actualiser', 'folder-open-symbolic':'Ouvrir le dossier',
                      'document-open-symbolic':'Importer', 'emblem-synchronizing-symbolic':'Synchroniser',
                      'image-x-generic-symbolic':'Couvertures'}
            caption = caption or labels.get(icon)
            if caption:
                button._cifereca_action_label = caption
                button._cifereca_action_icon = icon or {'Historique':'document-open-recent-symbolic', 'Réservations':'bookmark-new-symbolic'}.get(caption, 'view-more-symbolic')
                try:
                    content = Adw.ButtonContent(); content.set_label(caption); content.set_icon_name(button._cifereca_action_icon)
                    button.set_child(content)
                except (AttributeError, TypeError): button.set_label(caption)
        self._buttons.append(button); self.box.append(button)
        button.set_valign(Gtk.Align.CENTER)
        self._widths[button] = max(button.measure(Gtk.Orientation.HORIZONTAL, -1)[1], 32)
        self._compact = None

    def clear(self):
        for button in self._buttons: self.box.remove(button)
        self._buttons.clear(); self._widths.clear(); self._compact = None

    def _resize(self, _widget, _clock):
        source = self.source or self.get_parent()
        if source is None or source.get_width() <= 0: return True
        budget = action_budget(source.get_width(), self.fraction)
        if budget != self._budget:
            self.set_size_request(budget, -1); self._budget = budget
        compact = compact_actions(self._widths.values(), budget)
        if compact != self._compact:
            for button in self._buttons:
                label = getattr(button, '_cifereca_action_label', None)
                if not label: continue  # Existing icon-only controls keep their tooltip.
                icon = button._cifereca_action_icon
                if compact:
                    button.set_child(Gtk.Image.new_from_icon_name(icon))
                else:
                    try:
                        content = Adw.ButtonContent(); content.set_label(label); content.set_icon_name(icon)
                        button.set_child(content)
                    except (AttributeError, TypeError):
                        button.set_label(label)
                button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            self._compact = compact
        return True


def mirror_sensitivity(first, second):
    """Keep duplicated technical-page actions locked during the same task."""
    def changed(source, _pspec, target):
        value = source.get_sensitive()
        if target.get_sensitive() != value: target.set_sensitive(value)
    first.connect('notify::sensitive', changed, second)
    second.connect('notify::sensitive', changed, first)
