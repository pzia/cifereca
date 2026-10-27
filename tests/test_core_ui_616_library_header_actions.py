from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"


def _method(name: str) -> str:
    if name == "_build_library_page":
        owner = ast.parse(MAIN.with_name("library_page_controller.py").read_text(encoding="utf-8"))
        return ast.unparse(next(n for n in owner.body if isinstance(n, ast.FunctionDef)
                                and n.name == "build_library_page"))
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_library_keeps_primary_search_and_refresh_visible():
    source = _method("_build_library_page")
    assert "search = Gtk.Button" in source and "Recherche" in source
    assert "refresh = Gtk.Button.new_from_icon_name" in source and "view-refresh-symbolic" in source
    assert "actions.append(refresh)" in source


def test_open_folder_stays_direct_and_understandable():
    source = _method("_build_library_page")
    assert "folder = Gtk.Button.new_from_icon_name" in source and "folder-open-symbolic" in source
    assert "Ouvrir le dossier de la bibliothèque" in source
    assert "actions.append(folder)" in source


def test_rare_library_operations_are_grouped_in_named_menu():
    source = _method("_build_library_page")
    assert "advanced = Gtk.MenuButton" in source and "Opérations…" in source
    assert "Opérations avancées et maintenance" in source
    assert "Réinspecter les couvertures" in source
    assert "Importer un ACSM…" in source
    assert "Gérer la synchronisation Kobo…" in source
    assert "advanced_box.append(covers)" in source
    assert "advanced_box.append(acsm)" in source
    assert "advanced_box.append(acquire)" in source


def test_library_kobo_action_is_only_safe_navigation_to_sync():
    source = _method("_build_library_page")
    assert "Gérer la synchronisation Kobo" in source
    assert "window.navigate('sync')" in source
    assert "self.run_kobo_backup(" not in source
    assert "aucun téléchargement ne démarre depuis Bibliothèque" in source


def test_underlying_business_callbacks_are_unchanged():
    source = _method("_build_library_page")
    assert "window.refresh_library(True, covers, refresh_covers=True)" in source
    assert "window.choose_acsm()" in source
    assert "window.navigate('sync')" in source
    assert "window.open_library_folder()" in source

def test_sync_page_explains_inventory_and_distinct_opt_in_downloads():
    path = ROOT / "src/cifereca/gui/sync_controller.py"
    text = path.read_text(encoding="utf-8")
    assert "Synchroniser actualise les notices et droits en ligne" in text
    assert "explicitement autorisé les téléchargements automatiques" in text
    assert "par exemple les prêts BnF" in text
    assert "cette action peut aussi récupérer des fichiers" in text
    assert "Ces autorisations se règlent séparément dans chaque compte" in text
    assert "sans télécharger de livre" not in text
    assert "self.service.sync_kobo_backup(progress=progress)" in text
    assert "self.service.sync_kobo_backup(" not in _method("_build_library_page")
