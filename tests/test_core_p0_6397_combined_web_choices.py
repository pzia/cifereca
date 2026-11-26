"""#6397: mixed C web inventory, selected access mode stays independent."""
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.notice_c_authenticated_web_choices import AuthenticatedWebChoice
from cifereca.core.notice_c_combined_web_choices import read_c_web_choices, resolve_c_web_choice
from cifereca.core.notice_c_web_choices import PublicWebChoice
from cifereca.core.search_orchestration import capture_search_read_scope
from cifereca.gui.notice_c_web_action_controller import request_public_web_actions
from cifereca.storage import Storage
from tests.test_core_p0_6397_authenticated_web_choices import Host, URL
from tests.test_core_p0_6397_c_web_reference_persistence import source, link, publish
from tests.test_core_p0_6397_known_search_web_actions import GTK, deliver

PUBLIC = "https://books.example.org/title/mixed"


def setup(db):
    host = Host(db)
    alice = host.account("alice")
    bob = host.account("bob")
    public = source(db, "seller-a", "a", [link("seller-a", PUBLIC)])
    target = public.observation.key.edition_id
    publish(db, target, public)
    host.source(alice, group=target)
    _, canonical = host.source(bob, group=target, url=URL + "-bob")
    return host, alice, bob, canonical


def menu(host, c, owner="alice"):
    return read_c_web_choices(
        host, c.document, reader_scope=capture_search_read_scope(host, owner),
        expected_epoch=c.epoch,
    )


def test_mixed_choice_resolves_only_its_own_mode_and_reader(tmp_path):
    with closing(Storage(tmp_path / "mixed.sqlite")) as db:
        host, alice, bob, c = setup(db)
        m = menu(host, c)
        assert len(m.choices) == 2
        public, private = m.choices
        assert type(public) is PublicWebChoice and type(private) is AuthenticatedWebChoice
        assert resolve_c_web_choice(host, m, choice_id=public.choice_id) == PUBLIC
        assert resolve_c_web_choice(host, m, choice_id=private.choice_id) == URL
        other = menu(host, c, "bob")
        assert resolve_c_web_choice(host, other, choice_id=private.choice_id) is None
        assert resolve_c_web_choice(host, other, choice_id=other.choices[1].choice_id) == URL + "-bob"
        host._sessions[(alice.provider_id, alice.credential_id)].connected = False
        assert resolve_c_web_choice(host, m, choice_id=private.choice_id) is None
        assert resolve_c_web_choice(host, m, choice_id=public.choice_id) == PUBLIC
        assert len(menu(host, c).choices) == 1
        assert db.entitlements() == db.acquisitions() == db.placements() == []


def test_mixed_gtk_has_one_action_and_no_private_url_in_label(tmp_path):
    with closing(Storage(tmp_path / "gtk.sqlite")) as db:
        core, alice, bob, c = setup(db)
        scope = capture_search_read_scope(core, "alice")
        pending, actions, launches, toasts = [], [], [], []
        host = NS(service=core, _closed=False,
                  runner=NS(submit=lambda _label, work, done, fail, **kw:
                            pending.append((work, done, fail))),
                  toast=toasts.append)
        panel = NS(add_actions=actions.extend)
        gio = NS(AppInfo=NS(launch_default_for_uri=lambda uri, _: launches.append(uri)))
        assert request_public_web_actions(
            host, panel, c.document, expected_epoch=c.epoch, current=lambda: True,
            Gtk=GTK, Gio=gio, reader_scope=scope,
        )
        deliver(pending)
        root, = actions
        assert root.label == "Ouvrir la fiche sur…"
        public, private = root.popover.child.child.children
        assert "Connexion requise" in private.label
        assert URL not in private.label and "card-alice" not in private.label
        private.callback(private)
        deliver(pending)
        assert launches == [URL]
        core._sessions[(alice.provider_id, alice.credential_id)].connected = False
        private.callback(private)
        deliver(pending)
        assert launches == [URL] and toasts
        public.callback(public)
        deliver(pending)
        assert launches == [URL, PUBLIC]
        assert db.entitlements() == db.acquisitions() == db.placements() == []


@pytest.mark.parametrize("phase", ["publication", "browser"])
@pytest.mark.parametrize("revoke", ["disconnect", "epoch", "replacement"])
def test_mixed_private_completion_rechecks_session_but_public_remains_usable(tmp_path, phase, revoke):
    with closing(Storage(tmp_path / "mixed-fence.sqlite")) as db:
        core, alice, _, c = setup(db)
        scope = capture_search_read_scope(core, "alice")
        pending, actions, launches, toasts = [], [], [], []
        host = NS(service=core, _closed=False,
                  runner=NS(submit=lambda _label, work, done, fail, **kw:
                            pending.append((work, done, fail))),
                  toast=toasts.append)
        panel = NS(add_actions=actions.extend)
        gio = NS(AppInfo=NS(launch_default_for_uri=lambda uri, _: launches.append(uri)))
        assert request_public_web_actions(
            host, panel, c.document, expected_epoch=c.epoch, current=lambda: True,
            Gtk=GTK, Gio=gio, reader_scope=scope,
        )
        key = (alice.provider_id, alice.credential_id)
        session = core._sessions[key]

        def revoke_session():
            if revoke == "disconnect":
                session.connected = False
            elif revoke == "epoch":
                session._epoch += 1
            else:
                from tests.test_core_p0_6397_authenticated_web_choices import Offline
                core._sessions[key] = Offline(alice)

        if phase == "publication":
            work, done, _ = pending.pop(0)
            receipt = work()
            assert len(receipt.choices) == 2
            revoke_session()
            done(receipt)
            public, = actions
            assert public.label == "Ouvrir la fiche web"
        else:
            deliver(pending)
            root, = actions
            public, private = root.popover.child.child.children
            private.callback(private)
            work, done, _ = pending.pop(0)
            uri = work()
            assert uri == URL
            revoke_session()
            done(uri)
            assert not launches and toasts

        public.callback(public)
        deliver(pending)
        assert launches == [PUBLIC]
        assert db.entitlements() == db.acquisitions() == db.placements() == []
