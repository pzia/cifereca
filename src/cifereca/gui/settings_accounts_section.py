"""Static Settings headers for identities and provider accounts (P0 #1127).

These helpers only build GTK widgets. MainWindow remains responsible for
identity/ADEPT provenance, account-editor creation, callbacks, dirty revisions,
staged persistence and reconnect. The module is importable without Gtk.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class ProviderAccountsHeader:
    plugins_button: Any
    accounts_container: Any


def build_identity_settings_header(
    *, section: Any, Gtk: Any, add_css: Callable[..., Any],
) -> Any:
    """Retain warning, heading and hint before the per-identity editors."""
    warning = Gtk.Label(
        label="Les credentials fournisseurs sont actuellement stockés en clair dans le fichier de configuration local (permissions 0600). Ils ne sont jamais inclus dans les diagnostics.",
        xalign=0,
    )
    warning.set_wrap(True)
    add_css(warning, "caption", "dim-label")
    section.append(warning)

    identities_title = Gtk.Label(label="Identités", xalign=0)
    add_css(identities_title, "book-title")
    section.append(identities_title)

    identities_hint = Gtk.Label(
        label="Une identité regroupe ses connexions fournisseurs, ses préférences et son profil ADE. Les secrets ADE restent gérés par Kobo-Adept.",
        xalign=0,
    )
    identities_hint.set_wrap(True)
    add_css(identities_hint, "caption", "dim-label")
    section.append(identities_hint)
    identities_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    section.append(identities_container)
    return identities_container


def build_provider_accounts_header(
    *, section: Any, Gtk: Any, add_css: Callable[..., Any],
) -> ProviderAccountsHeader:
    """Build the provider intro; do not connect the Plugins navigation."""
    public_info = Gtk.Button(label="Gérer les plugins et leurs capacités techniques…")
    public_info.set_tooltip_text(
        "Ouvrir Plugins pour l’activation et les capacités détaillées ; Diagnostic conserve l’état technique."
    )
    section.append(public_info)
    accounts_title = Gtk.Label(label="Sources et comptes fournisseurs", xalign=0)
    add_css(accounts_title, "book-title")
    section.append(accounts_title)
    accounts_hint = Gtk.Label(
        label="Ajoutez et configurez ici les comptes utilisés par vos identités. Les capacités internes des plugins restent dans Plugins et Diagnostic.",
        xalign=0,
    )
    accounts_hint.set_wrap(True)
    add_css(accounts_hint, "caption", "dim-label")
    section.append(accounts_hint)
    accounts_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    section.append(accounts_container)
    return ProviderAccountsHeader(
        plugins_button=public_info, accounts_container=accounts_container,
    )


def build_provider_account_buttons(
    *, section: Any, descriptors: Iterable[Any],
    public_ids: Iterable[str], Gtk: Any,
) -> list[tuple[Any, str]]:
    """Return action controls without callbacks, for the host to bind."""
    public = set(public_ids)
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    buttons: list[tuple[Any, str]] = []
    for descriptor in descriptors:
        if descriptor.state != "ready" or descriptor.id in public:
            continue
        label = (
            f"Ajouter un compte {descriptor.display_name}"
            if descriptor.credential_fields
            else f"Ajouter la source {descriptor.display_name}"
        )
        add = Gtk.Button(label=label)
        buttons.append((add, descriptor.id))
        row.append(add)
    section.append(row)
    return buttons
