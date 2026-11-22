"""#6397: GTK widget construction cannot publish an obsolete C web menu."""
from contextlib import closing
from types import SimpleNamespace as NS

import pytest

from cifereca.storage import Storage
from cifereca.gui.notice_c_web_action_controller import request_public_web_actions
from tests.test_core_p0_6397_c_web_reference_persistence import source, link, publish
from tests.test_core_p0_6397_known_search_web_actions import (
    URL, OTHER, GTK, Widget, deliver,
)


@pytest.mark.parametrize("multiple", [False, True])
def test_route_change_during_widget_construction_refuses_publication(tmp_path, multiple):
    with closing(Storage(tmp_path / "web-widget-reentry.sqlite")) as db:
        links = [link("seller-a", URL)]
        if multiple:
            links.append(link("seller-a", OTHER))
        observation = source(db, "seller-a", "a", links)
        canonical = publish(db, observation.observation.key.edition_id, observation)
        queued, actions, launches = [], [], []
        host = NS(service=NS(storage=db), runner=NS(
            submit=lambda _title, work, done, fail, **_kw:
                queued.append((work, done, fail))),
            _closed=False, _route_display_token=object(), toast=lambda _s: None)
        panel = NS(add_actions=actions.extend)

        class NavigatingWidget(Widget):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                host._route_display_token = object()

        gtk = NS(**vars(GTK))
        if multiple:
            gtk.MenuButton = NavigatingWidget
        else:
            gtk.Button = NavigatingWidget
        gio = NS(AppInfo=NS(launch_default_for_uri=lambda uri, _ctx: launches.append(uri)))
        assert request_public_web_actions(
            host, panel, canonical.document, expected_epoch=canonical.epoch,
            current=lambda: True, Gtk=gtk, Gio=gio,
        )
        deliver(queued)
        assert actions == [] and queued == [] and launches == []
        assert db.entitlements() == [] and db.acquisitions() == []
        # A deliberate new request on the current route can still publish.
        assert request_public_web_actions(
            host, panel, canonical.document, expected_epoch=canonical.epoch,
            current=lambda: True, Gtk=GTK, Gio=gio,
        )
        deliver(queued)
        assert len(actions) == 1
