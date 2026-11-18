"""Two-profile native shell witness for ui.page.lifecycle/1 (#4010).

Only synthetic widgets and local markers. The existing Search fixture owns
the real Core and the offline provider doubles; no new provider action is
requested here. The actual shell builds the public contributions and drives
the identity, route, rebuild and stale GTK callbacks.
"""
from unittest.mock import patch

from cifereca.builtin_ui.pages import Pages
from cifereca.plugin_api import UiPageContribution
from cifereca.plugin_api.page_lifecycle import PageLifecycleV1


def verify_page_lifecycle(window, Gtk):
    manager = window.plugin_manager
    assert manager is not None
    original = manager.page_contributions
    routes = ("lifecycle-witness-visible", "lifecycle-witness-hidden")
    entries = {route: [] for route in routes}
    actions = []

    def build(route):
        def factory(_host):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            label = Gtk.Label(label="")
            box.append(label)
            entry = {"label": label, "shows": [], "invalidations": [],
                     "button": None, "disposals": 0}

            def clear():
                label.set_label("")
                button = entry["button"]
                entry["button"] = None
                if button is not None:
                    box.remove(button)

            def invalidate(event):
                assert event.account_scope is None
                entry["invalidations"].append(event.identity_id)
                clear()

            def show(event):
                assert event.is_current()
                clear()
                entry["shows"].append(event)
                label.set_label(event.identity_id or "")
                button = Gtk.Button(label="Synthetic action")
                button.connect("clicked", lambda _button: actions.append(event.generation)
                               if event.is_current() else None)
                box.append(button)
                entry["button"] = button

            def dispose():
                entry["disposals"] += 1
                clear()

            box.cifereca_page_lifecycle_v1 = PageLifecycleV1(invalidate, show, dispose)
            entries[route].append(entry)
            return box
        return factory

    contributions = [
        UiPageContribution(route, "Lifecycle witness", order=900 + index,
                           factory=build(route))
        for index, route in enumerate(routes)
    ]

    def contributed():
        # Keep the no-plugins fixture's native recovery routes available.
        return list(original() or Pages().pages()) + contributions

    with patch.object(manager, "page_contributions", side_effect=contributed):
        window._rebuild_plugin_pages()
        visible, hidden = (entries[route][-1] for route in routes)
        assert window.service.current_identity.identity_id == "camille"
        window.navigate(routes[0])
        assert window.view_stack.get_visible_child_name() == routes[0]
        assert visible["label"].get_text() == "camille"
        assert len(visible["shows"]) == 1 and hidden["shows"] == []
        a_event = visible["shows"][-1]
        a_button = visible["button"]
        window._show_route(routes[0])
        assert len(visible["shows"]) == 1

        window._activate_identity("alex")
        assert visible["label"].get_text() == "alex"
        assert not a_event.is_current()
        a_button.emit("clicked")
        assert actions == []
        b_event = visible["shows"][-1]
        b_button = visible["button"]
        window._activate_identity("camille")
        assert visible["label"].get_text() == "camille"
        assert not a_event.is_current() and not b_event.is_current()
        assert [event.identity_id for event in visible["shows"]] == [
            "camille", "alex", "camille",
        ]
        assert hidden["shows"] == [] and hidden["label"].get_text() == ""
        assert hidden["invalidations"] == ["camille", "alex", "camille"]
        b_button.emit("clicked")
        assert actions == []
        current = visible["shows"][-1]
        visible["button"].emit("clicked")
        assert actions == [current.generation]

        window.navigate(routes[1])
        assert window.view_stack.get_visible_child_name() == routes[1]
        assert not current.is_current()
        assert hidden["label"].get_text() == "camille"
        assert len(hidden["shows"]) == 1
        hidden_event = hidden["shows"][-1]
        hidden_button = hidden["button"]
        window._show_route(routes[1])
        assert len(hidden["shows"]) == 1

        window._rebuild_plugin_pages()
        assert visible["disposals"] == hidden["disposals"] == 1
        assert not hidden_event.is_current(require_visible=False)
        hidden_button.emit("clicked")
        assert actions == [current.generation]
        replacement = entries[routes[1]][-1]
        assert replacement is not hidden
        assert replacement["label"].get_text() == "camille"
        assert replacement["shows"][-1].is_current()

    # Restore the normal native routes and dispose the synthetic pages.
    window._rebuild_plugin_pages()
    window.navigate("search")
    assert all(entry["disposals"] == 1
               for group in entries.values() for entry in group)
    assert not replacement["shows"][-1].is_current(require_visible=False)
