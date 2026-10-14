"""Notification bar renderer; the window owns signals and timer lifetime."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NotificationBar:
    revealer: Any
    label: Any
    close: Any


def build_notification_bar(*, Gtk, add_css) -> NotificationBar:
    """Build the existing hidden, closable bar without timers or callbacks."""
    revealer = Gtk.Revealer()
    revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
    revealer.set_reveal_child(False)
    bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    add_css(bar, "notification-bar")
    label = Gtk.Label(label="", xalign=0)
    label.set_hexpand(True)
    label.set_wrap(True)
    bar.append(label)
    close = Gtk.Button.new_from_icon_name("window-close-symbolic")
    close.set_tooltip_text("Fermer la notification")
    bar.append(close)
    revealer.set_child(bar)
    return NotificationBar(revealer=revealer, label=label, close=close)