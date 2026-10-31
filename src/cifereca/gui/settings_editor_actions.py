"""Settings editor loading and actions; explicit host, dirty and directory ports."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from .paris_adept_presentation import normal_paris_adept_summary
from .settings_accounts_section import (build_identity_settings_header, build_provider_accounts_header, build_provider_account_buttons)
from .settings_identity_editor import build_identity_settings_editor
from .settings_account_editor import build_provider_account_editor


def build_settings_editors(self, *, box, descriptors, public_ids,
                           mark_settings_dirty, library_directory, Gtk, Adw, add_css):
    identities_container = build_identity_settings_header(
        section=box, Gtk=Gtk, add_css=add_css,
    )
    identity_editors: list[dict[str, Any]] = []
    configured_identities = self.config.identities()

    def add_identity_editor(data: dict[str, Any] | None = None) -> None:
        data = dict(data or {})
        existing_ids = {str(editor["id"].get_text()).strip() for editor in identity_editors}
        identity_id = str(data.get("id") or "identite").strip() or "identite"
        base = identity_id
        index = 2
        while identity_id in existing_ids:
            identity_id = f"{base}-{index}"; index += 1
        adept_profile = str(
            data.get("adept_profile")
            or self.service.storage.adept_profile_binding_get("identity", identity_id)
            or ""
        )
        normal_subtitle = (
            normal_paris_adept_summary(
                self.service.normal_paris_adept_status(identity_id)
            )
            if data.get("id")
            else "À configurer · enregistrez l’identité pour vérifier ses liaisons."
        )
        editor, choose_directory, choose_adept, delete_button = build_identity_settings_editor(
            data=data, identity_id=identity_id,
            adept_profile=adept_profile, normal_subtitle=normal_subtitle,
            identities_container=identities_container, Gtk=Gtk, Adw=Adw,
        )
        identity_editors.append(editor)
        directory_row = editor["directory"]
        adept_row = editor["adept_profile"]
        choose_directory.connect(
            "clicked",
            lambda _b, entry=directory_row: self._choose_identity_directory(
                entry, Path(library_directory().strip() or "~/EBooks")
            ),
        )
        choose_adept.connect(
            "clicked", lambda button, entry=adept_row:
            self._choose_adept_profile(entry, button),
        )
        def delete_identity(_button, e=editor):
            e["deleted"] = True
            e["group"].set_visible(False)
            mark_settings_dirty("accounts")
        delete_button.connect("clicked", delete_identity)

    for configured_identity in configured_identities:
        add_identity_editor(configured_identity)
    add_identity = Gtk.Button(label="Ajouter une identité")
    def add_identity_clicked(_button):
        add_identity_editor({})
        mark_settings_dirty("accounts")
    add_identity.connect("clicked", add_identity_clicked); box.append(add_identity)

    account_section = build_provider_accounts_header(
        section=box, Gtk=Gtk, add_css=add_css,
    )
    account_section.plugins_button.connect("clicked", lambda _b: self.navigate("plugins"))
    accounts_container = account_section.accounts_container
    account_editors: list[dict[str, Any]] = []
    configured_rows = self.config.provider_accounts()

    def add_account_editor(data: dict[str, Any] | None = None, provider_id: str = "") -> None:
        data = dict(data or {})
        provider_id = str(data.get("provider") or provider_id).lower()
        descriptor = descriptors.get(provider_id)
        if descriptor is None or descriptor.state != "ready" or provider_id in public_ids:
            return
        existing_ids = {str(x.get("id")) for x in account_editors}
        if not data.get("id"):
            existing_ids |= {str(x.get("id")) for x in configured_rows}
        base_id = f"{provider_id}-default"
        account_id = str(data.get("id") or base_id)
        n = 2
        while account_id in existing_ids:
            account_id = f"{provider_id}-{n}"; n += 1

        editor, delete_button = build_provider_account_editor(
            data=data, provider_id=provider_id, account_id=account_id,
            descriptor=descriptor, configured_identities=configured_identities,
            accounts_container=accounts_container, config=self.config,
            Gtk=Gtk, Adw=Adw,
        )
        account_editors.append(editor)
        def delete_account(_button, e=editor):
            e["deleted"] = True
            e["group"].set_visible(False)
            mark_settings_dirty("accounts")
        delete_button.connect("clicked", delete_account)

    for configured in configured_rows:
        add_account_editor(configured, str(configured.get("provider") or ""))

    for add, provider_id in build_provider_account_buttons(
        section=box, descriptors=descriptors.values(),
        public_ids=public_ids, Gtk=Gtk,
    ):
        def add_account_clicked(_button, pid=provider_id):
            add_account_editor({}, pid)
            mark_settings_dirty("accounts")
        add.connect("clicked", add_account_clicked)
    return identity_editors, account_editors
