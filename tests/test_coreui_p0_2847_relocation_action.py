"""#2847: only inspection-observed file keys enter a CoreUI SQL relink.

Static source-contract checks supplement (do not replace) GTK/XTEST acceptance
and the SQLite/EPUB transaction/inventory tests on the underlying Core calls.
"""
from __future__ import annotations

import ast
from pathlib import Path


GUI = (Path(__file__).resolve().parents[1]
       / "src/cifereca/gui/library_inspection_view.py")


def _read():
    source = GUI.read_text(encoding="utf-8")
    ast.parse(source)
    return source


def test_relocation_candidate_is_selected_from_scoped_inspection_not_file_picker():
    source = _read()
    assert "'Choisir un fichier local observé pour relocaliser…'" in source
    assert "session.unconfirmed_files(" in source
    assert "offset=missing_file_offset[0], limit=20" in source
    assert "query=missing_files_search.get_text()[:160]" in source
    assert "row._missing_file_key = item.key" in source
    assert "missing_file_key[0] = row._missing_file_key" in source
    assert "session.preview_missing_relocation(*chosen)" in source
    assert "session.confirm_missing_repair(" in source
    assert "chosen[0], file_key=chosen[1], user_confirmed=True" in source
    assert "FileChooser" not in source
    assert "FileDialog" not in source


def test_relocation_requires_fresh_sha_preview_and_second_destructive_dialog():
    source = _read()
    assert "Gtk.Button(\n            label='Prévisualiser la relocalisation SQL…'" in source
    assert "Gtk.Button(\n            label='Confirmer la relocalisation SQL…'" in source
    assert "missing_relocate_confirm.set_visible(False)" in source
    assert "missing_relocation_ready[0] = ready" in source
    assert "result.get('status') == 'review_required'" in source
    assert "result.get('read_only') is True" in source
    assert "result.get('requires_explicit_confirmation') is True" in source
    assert "'Relocaliser ce placement SQLite ?'" in source
    assert "'aucun droit ne sera attribué par le SHA.'" in source
    assert "if response != 'apply':" in source
    assert "dialog.set_default_response('cancel')" in source
    assert "dialog.set_close_response('cancel')" in source
    assert "outcome.get('status') == 'relocated'" in source


def test_changed_selection_file_filter_or_profile_revokes_private_plan():
    source = _read()
    assert "reset_missing_file_choice()" in source
    assert "missing_version[0] += 1" in source
    assert "session.cancel_missing_repair()" in source
    assert "version != missing_version[0]" in source
    assert "chosen != (missing_case[0], missing_file_key[0])" in source
    assert "missing_relocate_review.set_sensitive(False)" in source
    assert "missing_relocate_confirm.set_sensitive(False)" in source
    # A keyboard/mouse selection cannot visually switch to a different file
    # while the frozen (path_key, file_key) plan is being hashed/confirmed.
    assert "missing_file_controls(False)" in source
    assert "missing_file_controls(True)" in source
    assert "missing_files_list.set_sensitive(enabled)" in source
    assert "missing_files_search.set_sensitive(enabled)" in source
    assert "self.clear()" in source and "self.start()" in source
    for forbidden in (
        "plan_token", ".unlink(", "os.remove(", "DELETE FROM placements",
        "storage.placement_put(", "Storage.relocate_local_path(",
        "relocate_missing_placement(",
    ):
        assert forbidden not in source
