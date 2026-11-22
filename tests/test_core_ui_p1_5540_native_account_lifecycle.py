"""#5540: real GTK declarative editors and actual Save binder, offline.

The service write port uses isolated Config.save (not a full LibraryService
migration). No supplier or runtime authentication is present in this recipe.
"""
import os
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from cifereca.config import Config
from cifereca.gui.settings_account_editor import build_provider_account_editor
from cifereca.models import Account
from cifereca.plugin_api import (
    AccountOptionField, BorrowerScopedOptionField, CredentialField,
    ProviderCapabilities, ProviderDescriptor,
)
from tests import test_settings_migration_099_dev2 as harness

pytestmark = pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="GTK display required")
PROVIDER = "synthetic-5540"


@pytest.mark.parametrize("width", [960, 1366, 1600])
def test_native_multi_account_settings_save_reopen_delete(width):
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib
    Adw.init()

    rig = harness.SaveSettings()
    rig.setUp()
    window = Gtk.Window(default_width=width, default_height=760)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    scroller.set_child(container)
    window.set_child(scroller)
    config = rig.config
    a, b = "stable-id", "profile-b"
    owners = [{"id": a, "directory_name": "ma-bibliotheque"},
              {"id": b, "directory_name": "profile-b"}]
    config.set("identities", owners)
    rig.service.identities.append(NS(identity_id=b, directory_name="profile-b"))
    rig.ns["identity_editors"][0]["directory"].text = "ma-bibliotheque"
    V = harness.Value
    rig.ns["identity_editors"].append(
        dict(id=V(b), label=V("Profil B"), directory=V("profile-b"),
             adept_profile=V(), languages=V("fr"), prefer_vo=V(active=False))
    )
    desc = ProviderDescriptor(
        id=PROVIDER, display_name="Prêteur synthétique", description="Fixture",
        state="ready", capabilities=ProviderCapabilities(
            authentication=True, multiple_credentials=True, loans=True,
        ),
        credential_fields=(CredentialField("username", "Identifiant"),
                           CredentialField("password", "Secret", secret=True)),
        interactive_authentication=True,
        background_authentication_option_key="consent",
        account_options=(
            AccountOptionField("consent", "Synchronisation", value_type="boolean", default="off"),
            AccountOptionField("ceiling", "Numérique", value_type="unsigned_integer",
                               default="4", min_value=0, max_value=99,
                               advisory_loan_medium="digital"),
        ),
        borrower_scoped_options=(BorrowerScopedOptionField(
            key="linked", label="Lecteur lié", branches=(
                ("digital", "Numérique"), ("physical", "Physique")),
            default="4", min_value=0, max_value=99, advisory_loan_limit=True,
        ),),
    )
    rig.ns["descriptors"][PROVIDER] = desc
    editors = rig.ns["account_editors"]
    def commit(payload):
        config.data = deepcopy(payload)
        config.save()
        return {"migrated": 0, "collisions": 0}
    rig.service.save_configuration.side_effect = commit

    def mount(data, credential, observed=()):
        ed, _delete = build_provider_account_editor(
            data=data, provider_id=PROVIDER, account_id=credential,
            descriptor=desc, configured_identities=owners,
            accounts_container=container, config=config, Gtk=Gtk, Adw=Adw,
            observed_borrowers=observed,
        )
        editors.append(ed)
        return ed

    def save():
        assert rig.ns["save_settings"]()
        args = rig.runner.submit.call_args.args
        args[2](args[1]())
        return {row["id"]: row for row in Config(config.path).provider_accounts()}

    def remount(saved, observed=()):
        while container.get_first_child() is not None:
            container.remove(container.get_first_child())
        editors.clear()
        return [mount(row, key, observed) for key, row in saved.items()]

    try:
        # Two credentials on one identity, a third on another. All initially
        # disabled; merely creating UI fields never opts in to SSO.
        for credential, owner, ceiling in (
            ("conn-a", a, "0"), ("conn-b", a, "4"), ("conn-c", b, "99"),
        ):
            ed = mount({}, credential)
            assert not ed["enabled"].get_active()
            assert ed["option_fields"]["consent"].get_text() == "off"
            assert isinstance(ed["fields"]["password"], Adw.PasswordEntryRow)
            ed["identity"].set_text(owner)
            ed["fields"]["username"].set_text("reader-" + credential)
            ed["fields"]["password"].set_text("test-secret-" + credential)
            ed["enabled"].set_active(True)
            ed["option_fields"]["ceiling"].set_text(ceiling)
        assert not config.path.exists()
        window.present()
        loop = GLib.MainLoop()
        GLib.timeout_add(250, lambda: (loop.quit(), False)[1])
        loop.run()
        for ed in editors:
            group = ed["group"]
            assert group.get_mapped() and group.get_width() > 0
            assert group.get_width() <= container.get_width()
        saved = save()
        assert set(saved) == {"conn-a", "conn-b", "conn-c"}
        assert [saved[key]["identity_id"] for key in saved] == [a, a, b]
        assert [saved[key]["options"]["ceiling"] for key in saved] == ["0", "4", "99"]
        assert all(row["options"]["consent"] == "off" for row in saved.values())

        # Same upstream reader ID under distinct connection/identity scopes
        # must not cross-populate. Only the observed matching rows are editable.
        borrowers = tuple(Account(
            unique_id="shared-linked", is_primary=False, identity_id=owner,
            provider_id=PROVIDER, credential_id=credential, display_name="Lecteur témoin",
        ) for credential, owner in (("conn-a", a), ("conn-c", b)))
        first, second, third = remount(saved, borrowers)
        assert second["scoped_option_fields"]["linked"] == {}
        first_fields = first["scoped_option_fields"]["linked"]["shared-linked"]
        third_fields = third["scoped_option_fields"]["linked"]["shared-linked"]
        first_fields["digital"].set_text("1")
        first_fields["physical"].set_text("2")
        third_fields["digital"].set_text("98")
        third_fields["physical"].set_text("97")
        third["option_fields"]["consent"].row.set_active(True)

        # Invalid value rejects the whole draft, leaving the existing file
        # byte-for-byte unchanged and never queuing a save.
        before = config.path.read_bytes()
        calls = rig.runner.submit.call_count
        first_fields["digital"].set_text("100")
        assert not rig.ns["save_settings"]()
        assert rig.runner.submit.call_count == calls
        assert config.path.read_bytes() == before
        first_fields["digital"].set_text("1")
        saved = save()
        assert saved["conn-a"]["options"]["linked"] == {
            "shared-linked": {"digital": "1", "physical": "2"}}
        assert saved["conn-c"]["options"]["linked"] == {
            "shared-linked": {"digital": "98", "physical": "97"}}
        assert saved["conn-a"]["options"]["consent"] == "off"
        assert saved["conn-c"]["options"]["consent"] == "on"

        # Reopen without connected borrowers: persisted options survive and
        # remain editable. Simulate the delete controller's explicit flag.
        first, second, third = remount(saved)
        assert first["scoped_option_fields"]["linked"]["shared-linked"]["digital"].get_text() == "1"
        assert third["scoped_option_fields"]["linked"]["shared-linked"]["physical"].get_text() == "97"
        assert second["scoped_option_fields"]["linked"] == {}
        second["deleted"] = True
        second["group"].set_visible(False)
        final = save()
        assert set(final) == {"conn-a", "conn-c"}
        for credential in final:
            assert final[credential] == saved[credential]
        rig.window.reconnect_sources.assert_not_called()
        rig.window._configure_kobo_backup_timer.assert_not_called()
    finally:
        window.destroy()
        rig.tearDown()
