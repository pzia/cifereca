"""GTK-only download policy editor for Settings → Bibliothèque (CoreUI #1127).

Keep the global/type/provider precedence and shell-quoted command display
unchanged. No GTK/PyGObject imports, filesystem/network operations, save
handlers, new signals, or DRM calls at import/build time. The host retains
dirty revision tracking, provider ownership and atomic async persistence.
"""
from __future__ import annotations

from dataclasses import dataclass
import shlex
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class LibraryDownloadSettings:
    download_group: Any
    global_dir_row: Any
    global_ebook_row: Any
    global_audio_row: Any
    global_cmd_row: Any
    global_post_row: Any
    provider_download_editors: dict[str, dict[str, Any]]


def build_library_download_section(
    *, section: Any, config: Any, descriptors: Mapping[str, Any],
    Gtk: Any, Adw: Any, add_css: Callable[..., Any],
) -> LibraryDownloadSettings:
    """Build the original 5+5-per-provider rows in their original order."""
    download_group = Adw.PreferencesGroup()
    download_group.set_title("Téléchargements — réglages globaux")
    section.append(download_group)
    global_downloads = dict(config.get("downloads", {}) or {})
    global_types = dict(global_downloads.get("content_type_directories", {}) or {})

    global_dir_row = Adw.EntryRow()
    global_dir_row.set_title("Répertoire global")
    global_dir_row.set_text(str(global_downloads.get("directory") or ""))
    download_group.add(global_dir_row)

    global_ebook_row = Adw.EntryRow()
    global_ebook_row.set_title("Répertoire livres numériques")
    global_ebook_row.set_text(str(global_types.get("ebook") or ""))
    download_group.add(global_ebook_row)

    global_audio_row = Adw.EntryRow()
    global_audio_row.set_title("Répertoire livres audio")
    global_audio_row.set_text(str(global_types.get("audiobook") or ""))
    download_group.add(global_audio_row)

    global_cmd_row = Adw.EntryRow()
    global_cmd_row.set_title("Commande de téléchargement (optionnelle)")
    global_cmd = global_downloads.get("command", []) or []
    global_cmd_row.set_text(" ".join(shlex.quote(str(x)) for x in global_cmd))
    download_group.add(global_cmd_row)

    global_post_row = Adw.EntryRow()
    global_post_row.set_title("Hook post-téléchargement (optionnel)")
    global_post = global_downloads.get("post_download_command", []) or []
    global_post_row.set_text(" ".join(shlex.quote(str(x)) for x in global_post))
    download_group.add(global_post_row)

    download_hint = Gtk.Label(
        label="Priorité des répertoires : type+plugin → plugin → type global → global. Placeholders commandes : {url} {file} {directory} {title} {record_id} {provider} {content_type} {format}.",
        xalign=0,
    )
    download_hint.set_wrap(True)
    add_css(download_hint, "caption", "dim-label")
    section.append(download_hint)

    provider_download_editors: dict[str, dict[str, Any]] = {}
    for descriptor in descriptors.values():
        if descriptor.state != "ready":
            continue
        settings = config.provider_settings(descriptor.id)
        dcfg = dict(settings.get("downloads", {}) or {})
        tcfg = dict(dcfg.get("content_type_directories", {}) or {})
        group = Adw.PreferencesGroup()
        group.set_title(f"Téléchargements — {descriptor.display_name}")
        section.append(group)

        drow = Adw.EntryRow()
        drow.set_title("Répertoire du plugin")
        drow.set_text(str(dcfg.get("directory") or ""))
        group.add(drow)

        erow = Adw.EntryRow()
        erow.set_title("Répertoire livres numériques")
        erow.set_text(str(tcfg.get("ebook") or ""))
        group.add(erow)

        arow = Adw.EntryRow()
        arow.set_title("Répertoire livres audio")
        arow.set_text(str(tcfg.get("audiobook") or ""))
        group.add(arow)

        crow = Adw.EntryRow()
        crow.set_title("Commande de téléchargement (surcharge)")
        crow.set_text(" ".join(shlex.quote(str(x)) for x in (dcfg.get("command", []) or [])))
        group.add(crow)

        prow = Adw.EntryRow()
        prow.set_title("Hook post-téléchargement du plugin")
        prow.set_text(" ".join(shlex.quote(str(x)) for x in (dcfg.get("post_download_command", []) or [])))
        group.add(prow)

        provider_download_editors[descriptor.id] = {
            "directory": drow, "ebook": erow, "audiobook": arow,
            "command": crow, "post": prow,
        }

    return LibraryDownloadSettings(
        download_group=download_group, global_dir_row=global_dir_row,
        global_ebook_row=global_ebook_row, global_audio_row=global_audio_row,
        global_cmd_row=global_cmd_row, global_post_row=global_post_row,
        provider_download_editors=provider_download_editors,
    )
