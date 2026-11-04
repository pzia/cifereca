"""P1 #3731: corrupt provider download settings never become commands or saves.

The builder must remain provider-neutral and display a safe, non-sensitive
placeholder when historical configuration has the wrong type. The async
Settings binder independently refuses to overwrite it with those placeholders.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from tests.test_core_ui_1127_library_downloads import _render


@pytest.mark.parametrize("saved", [
    None, [], "not-a-mapping", 42, True,
    {"content_type_directories": ["ebook", "untrusted"]},
    {"content_type_directories": {"ebook": 22}},
    {"directory": ["outside"]},
    {"command": "curl https://private.invalid/?token=SECRET"},
    {"command": {"secret": "PRIVATE"}},
    {"command": ["curl", 42]},
    {"command": ["curl", "PRIVATE\x00SECRET"]},
    {"post_download_command": ["run", None]},
    {"post_download_command": [1]},
])
def test_malformed_global_download_policy_is_safe_and_marked(saved):
    raw = {"downloads": deepcopy(saved)}
    before = deepcopy(raw)
    ui, section, _, provider_values = _render(data=raw)
    assert ui.invalid_saved_snapshot is True
    assert raw == before
    assert ui.global_cmd_row.text == "" or "PRIVATE" not in ui.global_cmd_row.text
    assert "PRIVATE" not in ui.global_post_row.text
    assert "SECRET" not in ui.global_cmd_row.text
    assert all(type(row.text) is str for row in (
        ui.global_dir_row, ui.global_ebook_row, ui.global_audio_row,
        ui.global_cmd_row, ui.global_post_row,
    ))


@pytest.mark.parametrize("saved", [
    None, ["unexpected"], "curl --secret=PRIVATE", 1,
    {"command": ["curl", {"token": "PRIVATE"}]},
    {"content_type_directories": "PRIVATE"},
    {"content_type_directories": {"audiobook": ["PRIVATE"]}},
    {"directory": {"private": "PRIVATE"}},
    {"post_download_command": ["do", True]},
])
def test_malformed_provider_only_policy_flags_entire_settings_draft(saved):
    providers = {"paris": {"downloads": deepcopy(saved)}}
    before = deepcopy(providers)
    ui, section, _, _ = _render(providers=providers)
    assert ui.invalid_saved_snapshot is True
    assert providers == before
    edits = ui.provider_download_editors["paris"]
    assert all(type(row.text) is str for row in edits.values())
    assert all("PRIVATE" not in row.text for row in edits.values())
    assert ui.global_cmd_row.text == ""


@pytest.mark.parametrize("corrupt", [
    None, True, [], "PRIVATE", 7,
])
def test_malformed_provider_settings_root_is_not_replaced_on_unrelated_save(
    corrupt,
):
    # This shape mirrors a corrupt JSON Config providers.settings value.
    ui, section, _, _ = _render(
        data={"providers.settings": corrupt}, providers={},
    )
    assert ui.invalid_saved_snapshot is True
    assert all(row.text == "" for row in
               ui.provider_download_editors["paris"].values())


def test_explicitly_null_provider_container_is_not_treated_as_missing():
    ui, section, _, _ = _render(providers={"paris": None})
    assert ui.invalid_saved_snapshot is True
    assert all(row.text == "" for row in
               ui.provider_download_editors["paris"].values())


def test_valid_provider_command_and_opaque_extensions_remain_unchanged():
    providers = {"paris": {"downloads": {
        "directory": "Books", "content_type_directories": {
            "ebook": "EPUB", "audiobook": "Audio", "comic": "COMICS",
        },
        "command": ["tool", "--path", "a b"],
        "post_download_command": ["after", "it is done"],
        "future_plugin_field": {"enabled": False},
    }}}
    before = deepcopy(providers)
    ui, section, _, _ = _render(providers=providers)
    assert ui.invalid_saved_snapshot is False
    edit = ui.provider_download_editors["paris"]
    assert edit["directory"].text == "Books"
    assert edit["command"].text == "tool --path 'a b'"
    assert edit["post"].text == "after 'it is done'"
    assert providers == before


def test_missing_provider_download_policy_is_neutral_and_not_invalid():
    ui, section, _, _ = _render(providers={})
    assert ui.invalid_saved_snapshot is False
    assert all(row.text == "" for row in
               ui.provider_download_editors["paris"].values())
