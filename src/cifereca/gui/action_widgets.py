"""Consistent buttons and adaptive action bars for built-in pages."""
from __future__ import annotations

from gi.repository import Adw, Gtk
from .action_layout import action_budget, compact_actions, visible_action_count


def action_button(
    label: str,
    icon_name: str | None = None,
    *,
    suggested: bool = False,
    destructive: bool = False,
    tooltip: str | None = None,
) -> Gtk.Button:
    """Create a consistently labelled action button with an optional icon."""
    button = Gtk.Button()
    button._cifereca_action_label = label
    button._cifereca_action_icon = icon_name or 'view-more-symbolic'
    if icon_name:
        try:
            content = Adw.ButtonContent()
            content.set_label(label)
            content.set_icon_name(icon_name)
            button.set_child(content)
        except (AttributeError, TypeError):
            # Keep the action usable on older libadwaita runtimes where
            # ButtonContent is not available. The label remains authoritative.
            button.set_label(label)
    else:
        button.set_label(label)
    if suggested:
        button.add_css_class("suggested-action")
    if destructive:
        button.add_css_class("destructive-action")
    button.set_tooltip_text(tooltip or label)
    return button


class AdaptiveActionBar(Gtk.ScrolledWindow):
    """Half-page budget; keep labelled actions, compact on overflow.

    The last fallback is horizontal scrolling when even icons do not fit.
    Buttons and their callbacks stay the same objects through every resize.
    """
    def __init__(
        self, *, fraction=0.5, source=None,
        overflow=False, preserve_primary_label=False,
    ):
        super().__init__()
        self.fraction = fraction; self.source = source
        self.overflow = bool(overflow)
        self.preserve_primary_label = bool(preserve_primary_label)
        self._buttons = []; self._widths = {}; self._compact = None; self._budget = None
        self._overflow_visible_count = None
        self._overflow_width = None
        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.box.add_css_class('toolbar')
        self.box.set_halign(Gtk.Align.END)
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.set_propagate_natural_width(False)
        self.set_min_content_width(0)
        self.set_valign(Gtk.Align.START)
        self.set_child(self.box)
        if self.overflow:
            self.overflow_button = Gtk.MenuButton()
            self.overflow_button.set_icon_name('view-more-symbolic')
            self.overflow_button.set_tooltip_text('Plus d’actions')
            try:
                self.overflow_button.update_property(
                    [Gtk.AccessibleProperty.LABEL], ['Plus d’actions']
                )
            except (AttributeError, TypeError):
                pass
            self.overflow_popover = Gtk.Popover()
            self.overflow_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            self.overflow_box.set_margin_top(8); self.overflow_box.set_margin_bottom(8)
            self.overflow_box.set_margin_start(8); self.overflow_box.set_margin_end(8)
            self.overflow_popover.set_child(self.overflow_box)
            self.overflow_button.set_popover(self.overflow_popover)
            self.overflow_button.set_visible(False)
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
        self._overflow_visible_count = None

    def clear(self):
        for button in self._buttons:
            parent = button.get_parent()
            if parent is self.box:
                self.box.remove(button)
            elif self.overflow and parent is self.overflow_box:
                self.overflow_box.remove(button)
        self._buttons.clear(); self._widths.clear(); self._compact = None
        self._overflow_visible_count = None
        if self.overflow:
            parent = self.overflow_button.get_parent()
            if parent is self.box:
                self.box.remove(self.overflow_button)
            self.overflow_button.set_visible(False)

    @staticmethod
    def _move_button(button, target):
        parent = button.get_parent()
        if parent is target:
            return
        if parent is not None:
            remover = getattr(parent, 'remove', None)
            if callable(remover):
                remover(button)
        target.append(button)

    def _layout_overflow(self, budget):
        # append() runs before rooting/CSS resolution. Refresh widths in the
        # toolbar context, retaining those measurements for popover children.
        for button in self._buttons:
            if button.get_parent() is self.box:
                self._widths[button] = max(button.measure(Gtk.Orientation.HORIZONTAL, -1)[1], 32)
        budget = max(0, budget - self.box.get_margin_start() - self.box.get_margin_end())
        # Measure the real menu button (including its theme), not a fixed icon
        # width. It must be visible and parented for GTK to measure it.
        if self.overflow_button.get_visible():
            self._overflow_width = self.overflow_button.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        elif self._overflow_width is None:
            self.box.append(self.overflow_button)
            self.overflow_button.set_visible(True)
            self._overflow_width = self.overflow_button.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            self.box.remove(self.overflow_button)
            self.overflow_button.set_visible(False)
        widths = [self._widths[button] for button in self._buttons]
        visible_count = visible_action_count(
            widths, budget, spacing=self.box.get_spacing(),
            overflow_width=self._overflow_width, minimum_visible=1,
        )
        if visible_count == self._overflow_visible_count:
            return
        if self.overflow_button.get_parent() is self.box:
            self.box.remove(self.overflow_button)
        hidden = self._buttons[visible_count:]
        for button in self._buttons[:visible_count]:
            self._move_button(button, self.box)
        previous = None
        for button in hidden:
            self._move_button(button, self.overflow_box)
            self.overflow_box.reorder_child_after(button, previous)
            previous = button
        parent = self.overflow_button.get_parent()
        if parent is not None and parent is not self.box:
            remover = getattr(parent, 'remove', None)
            if callable(remover):
                remover(self.overflow_button)
        if hidden:
            if self.overflow_button.get_parent() is not self.box:
                self.box.append(self.overflow_button)
            self.overflow_button.set_visible(True)
        else:
            if self.overflow_button.get_parent() is self.box:
                self.box.remove(self.overflow_button)
            self.overflow_button.set_visible(False)
        self._overflow_visible_count = visible_count

    @staticmethod
    def _compact_label(label, icon, icon_count):
        if icon_count <= 1:
            return None
        text = str(label or '')
        if icon == 'document-send-symbolic' and text.casefold().startswith('envoyer à '):
            destination = text[len('Envoyer à '):].strip()
            return f'→ {destination}' if destination else text
        return text

    def _resize(self, _widget, _clock):
        source = self.source or self.get_parent()
        if source is None or source.get_width() <= 0: return True
        budget = action_budget(source.get_width(), self.fraction)
        if budget != self._budget:
            self.set_size_request(budget, -1); self._budget = budget
        if self.overflow:
            self._layout_overflow(budget)
            if self.preserve_primary_label and self._buttons and self._compact is not False:
                primary = self._buttons[0]
                label = getattr(primary, '_cifereca_action_label', None)
                icon = getattr(primary, '_cifereca_action_icon', None)
                if label and icon:
                    try:
                        content = Adw.ButtonContent()
                        content.set_label(label); content.set_icon_name(icon)
                        primary.set_child(content)
                    except (AttributeError, TypeError):
                        primary.set_label(label)
                    primary.update_property([Gtk.AccessibleProperty.LABEL], [label])
                self._compact = False
            return True
        compact = compact_actions(self._widths.values(), budget)
        if compact != self._compact:
            icon_counts = {}
            for button in self._buttons:
                icon = getattr(button, '_cifereca_action_icon', None)
                if icon:
                    icon_counts[icon] = icon_counts.get(icon, 0) + 1
            for button in self._buttons:
                label = getattr(button, '_cifereca_action_label', None)
                if not label: continue  # Existing icon-only controls keep their tooltip.
                icon = button._cifereca_action_icon
                compact_label = self._compact_label(label, icon, icon_counts.get(icon, 0))
                if compact and compact_label is None:
                    button.set_child(Gtk.Image.new_from_icon_name(icon))
                else:
                    shown_label = compact_label if compact else label
                    try:
                        content = Adw.ButtonContent(); content.set_label(shown_label); content.set_icon_name(icon)
                        button.set_child(content)
                    except (AttributeError, TypeError):
                        button.set_label(shown_label)
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


def clear_box(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt

def add_css(widget: Gtk.Widget, *classes: str) -> Gtk.Widget:
    for name in classes:
        widget.add_css_class(name)
    return widget
