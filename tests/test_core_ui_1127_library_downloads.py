"""P0 #1127: headless GTK doubles for the real Library/Downloads builder."""
from __future__ import annotations
from tests.settings_source import settings_source_text

import ast
from copy import deepcopy
from pathlib import Path
import shlex
from types import SimpleNamespace

from cifereca.gui.settings_library_downloads import build_library_download_section


class Widget:
    def __init__(self, *, label=None, xalign=None):
        self.label = label
        self.xalign = xalign
        self.title = None
        self.text = None
        self.children = []
        self.wrap = False
        self.classes = []
        self.signals = []
    def set_title(self, text): self.title = text
    def set_text(self, text): self.text = text
    def set_wrap(self, value): self.wrap = value
    def add(self, child): self.children.append(child)
    def append(self, child): self.children.append(child)
    def connect(self, *args): self.signals.append(args)


def _render(data=None, providers=None):
    data = data or {}
    provider_values = providers if providers is not None else {
        "paris": {"downloads": {
            "directory": "Paris", "content_type_directories": {
                "ebook": "EPUB", "audiobook": "Audio",
            }, "command": ["fetch", "--file", "a b"],
            "post_download_command": ["after", "ok"],
        }},
    }
    descriptors = {
        "paris": SimpleNamespace(id="paris", display_name="Paris", state="ready"),
        "kobo": SimpleNamespace(id="kobo", display_name="Kobo", state="unavailable"),
    }
    config = SimpleNamespace(
        get=lambda key, default=None: data.get(key, default),
        provider_settings=lambda provider_id: provider_values.get(provider_id, {}),
    )
    section = Widget()
    css_calls = []
    controls = build_library_download_section(
        section=section, config=config, descriptors=descriptors,
        Gtk=SimpleNamespace(Label=Widget),
        Adw=SimpleNamespace(PreferencesGroup=Widget, EntryRow=Widget),
        add_css=lambda widget, *classes: (
            widget.classes.extend(classes), css_calls.append(tuple(classes))
        ),
    )
    return controls, section, css_calls, provider_values


def test_global_section_preserves_original_order_legend_and_defaults():
    controls, section, css, _ = _render()
    assert section.children[0] is controls.download_group
    assert controls.download_group.title == "Téléchargements — réglages globaux"
    assert [r.title for r in controls.download_group.children] == [
        "Répertoire global", "Répertoire livres numériques",
        "Répertoire livres audio", "Commande de téléchargement (optionnelle)",
        "Hook post-téléchargement (optionnel)",
    ]
    assert controls.global_dir_row.text == ""
    assert controls.global_ebook_row.text == ""
    assert controls.global_audio_row.text == ""
    assert controls.global_cmd_row.text == ""
    assert controls.global_post_row.text == ""
    assert section.children[1].label.startswith("Priorité des répertoires :")
    assert section.children[1].wrap is True
    assert css == [("caption", "dim-label")]


def test_provider_override_has_same_fields_and_skips_unready_plugins():
    controls, section, _, _ = _render()
    assert list(controls.provider_download_editors) == ["paris"]
    group = section.children[2]
    assert group.title == "Téléchargements — Paris"
    assert [r.title for r in group.children] == [
        "Répertoire du plugin", "Répertoire livres numériques",
        "Répertoire livres audio", "Commande de téléchargement (surcharge)",
        "Hook post-téléchargement du plugin",
    ]
    rows = controls.provider_download_editors["paris"]
    assert rows["directory"].text == "Paris"
    assert rows["ebook"].text == "EPUB"
    assert rows["audiobook"].text == "Audio"
    assert rows["command"].text == "fetch --file 'a b'"
    assert rows["post"].text == "after ok"


def test_global_shell_tokens_are_displayed_with_identical_shlex_quoting():
    values = {"downloads": {
        "directory": "/home/me/books",
        "content_type_directories": {"ebook": "E", "audiobook": "A"},
        "command": ["script", "--path", "a b", "it's fine", "{file}"],
        "post_download_command": ["on-complete", "--title", "épreuve française"],
    }}
    controls, _, _, _ = _render(data=values)
    assert controls.global_dir_row.text == "/home/me/books"
    assert controls.global_ebook_row.text == "E"
    assert controls.global_audio_row.text == "A"
    assert shlex.split(controls.global_cmd_row.text) == values["downloads"]["command"]
    assert shlex.split(controls.global_post_row.text) == values["downloads"]["post_download_command"]


def test_provider_settings_do_not_mutate_and_no_signals_or_network():
    original = {"paris": {"downloads": {"command": ["x"], "post_download_command": []}}}
    copy = deepcopy(original)
    controls, section, _, providers = _render(providers=original)
    assert providers == copy
    assert controls.provider_download_editors["paris"]["command"].text == "x"
    assert all(not item.signals for group in section.children
               for item in [group, *group.children])


def test_missing_provider_settings_keep_blank_overrides_without_inference():
    controls, section, _, _ = _render(providers={})
    assert list(controls.provider_download_editors) == ["paris"]
    assert all(row.text == "" for row in
               controls.provider_download_editors["paris"].values())
    assert len(section.children) == 3


def test_main_window_retains_async_saving_and_global_library_row_reference():
    root = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
    source = ast.unparse(ast.parse(settings_source_text()))
    assert "build_library_download_section(" in source
    assert "download_group = download_settings.download_group" in source
    assert "provider_download_editors = download_settings.provider_download_editors" in source
    assert "library_dir_row = kobo_settings.library_dir_row" in source
    assert "save_settings" in source
    assert "settings_committer.submit(" in source
    assert "SettingsRevisionState(" in source
