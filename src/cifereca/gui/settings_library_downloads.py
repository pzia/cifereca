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
    # Incomplete/ill-typed persisted configuration must never be silently
    # replaced by empty editor values during an unrelated Settings save.
    invalid_saved_snapshot: bool = False

    
def _download_mapping(raw: object) -> tuple[dict[str, Any], bool]:
    # An absent key already uses the {} default at every caller; an
    # explicitly persisted null is malformed and cannot be re-saved as {}.
    if type(raw) is not dict:
        return {}, True
    return raw, False


def _download_text(raw: dict[str, Any], field: str) -> tuple[str, bool]:
    if field not in raw:
        return "", False
    value = raw[field]
    return (value, False) if type(value) is str else ("", True)


def _download_command(raw: dict[str, Any], field: str) -> tuple[str, bool]:
    if field not in raw:
        return "", False
    value = raw[field]
    if (type(value) not in (list, tuple) or len(value) > 256
            or any(type(arg) is not str or "\x00" in arg
                   or len(arg) > 65536 for arg in value)):
        return "", True
    return " ".join(shlex.quote(arg) for arg in value), False


def build_library_download_section(
    *, section: Any, config: Any, descriptors: Mapping[str, Any],
    Gtk: Any, Adw: Any, add_css: Callable[..., Any],
) -> LibraryDownloadSettings:
    """Build the original 5+5-per-provider rows in their original order."""
    download_group = Adw.PreferencesGroup()
    download_group.set_title("Téléchargements — réglages globaux")
    section.append(download_group)
    global_downloads, invalid = _download_mapping(config.get("downloads", {}))
    # Config.provider_settings() itself normalizes a malformed provider
    # *root* to {}. A later set_provider_settings() would then discard the
    # entire historical root, so detect it before rendering any editor.
    _, bad_root = _download_mapping(config.get("providers.settings", {}))
    invalid |= bad_root
    global_types, bad = _download_mapping(
        global_downloads.get("content_type_directories", {}),
    )
    invalid |= bad

    def text_value(raw: dict[str, Any], key: str) -> str:
        nonlocal invalid
        value, bad_value = _download_text(raw, key)
        invalid |= bad_value
        return value

    def command_value(raw: dict[str, Any], key: str) -> str:
        nonlocal invalid
        value, bad_value = _download_command(raw, key)
        invalid |= bad_value
        return value

    global_dir_row = Adw.EntryRow()
    global_dir_row.set_title("Répertoire global")
    global_dir_row.set_text(text_value(global_downloads, "directory"))
    download_group.add(global_dir_row)

    global_ebook_row = Adw.EntryRow()
    global_ebook_row.set_title("Répertoire livres numériques")
    global_ebook_row.set_text(text_value(global_types, "ebook"))
    download_group.add(global_ebook_row)

    global_audio_row = Adw.EntryRow()
    global_audio_row.set_title("Répertoire livres audio")
    global_audio_row.set_text(text_value(global_types, "audiobook"))
    download_group.add(global_audio_row)

    global_cmd_row = Adw.EntryRow()
    global_cmd_row.set_title("Commande de téléchargement (optionnelle)")
    global_cmd_row.set_text(command_value(global_downloads, "command"))
    download_group.add(global_cmd_row)

    global_post_row = Adw.EntryRow()
    global_post_row.set_title("Hook post-téléchargement (optionnel)")
    global_post_row.set_text(
        command_value(global_downloads, "post_download_command")
    )
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
        settings_raw = config.provider_settings(descriptor.id)
        settings, bad = _download_mapping(settings_raw)
        invalid |= bad
        # Detect corrupt persisted provider containers even if Config's
        # provider_settings() accessor normalizes them to an empty dict.
        missing = object()
        historical = config.get(f"providers.settings.{descriptor.id}", missing)
        if historical is not missing and type(historical) is not dict:
            invalid = True
        dcfg, bad = _download_mapping(settings.get("downloads", {}))
        invalid |= bad
        tcfg, bad = _download_mapping(
            dcfg.get("content_type_directories", {}),
        )
        invalid |= bad
        group = Adw.PreferencesGroup()
        group.set_title(f"Téléchargements — {descriptor.display_name}")
        section.append(group)

        drow = Adw.EntryRow()
        drow.set_title("Répertoire du plugin")
        drow.set_text(text_value(dcfg, "directory"))
        group.add(drow)

        erow = Adw.EntryRow()
        erow.set_title("Répertoire livres numériques")
        erow.set_text(text_value(tcfg, "ebook"))
        group.add(erow)

        arow = Adw.EntryRow()
        arow.set_title("Répertoire livres audio")
        arow.set_text(text_value(tcfg, "audiobook"))
        group.add(arow)

        crow = Adw.EntryRow()
        crow.set_title("Commande de téléchargement (surcharge)")
        crow.set_text(command_value(dcfg, "command"))
        group.add(crow)

        prow = Adw.EntryRow()
        prow.set_title("Hook post-téléchargement du plugin")
        prow.set_text(command_value(dcfg, "post_download_command"))
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
        invalid_saved_snapshot=invalid,
    )
