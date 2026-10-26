"""Stale public search data cannot re-enable acquisition in detail or Core."""
import ast
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.search import file_format
from cifereca.gui.detail_model import (
    book_detail_presentation, detail_language,
)
from cifereca.providers import ProviderConfigurationError
from tests.test_search_results_view import _book
from tests.test_sync_state_contract import _service


def _stale(**metadata):
    return replace(_book("gallica"), owned=False, downloadable=False,
                   metadata={"search_provenance": "known_catalog_stale", **metadata})


def test_core_refuses_stale_acquisition_before_session_or_admission(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        def forbidden(*args, **kwargs):
            pytest.fail("stale result must not reach a session or admission callback")
        monkeypatch.setattr(service, "_session_for_source", forbidden)
        for provider in ("gallica", "kobo", "numilog", "gutenberg"):
            item = replace(_stale(), source_provider=provider)
            assert service.can_direct_download(item) is False
            with pytest.raises(ProviderConfigurationError, match="Résultat connu hors ligne"):
                service._acquire_direct_item_impl(item, _admission=forbidden)
    finally:
        service.close()


def _show_search():
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"
    tree = ast.parse(path.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DetailPanel")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "show_search")
    env = dict(
        file_format=file_format, detail_language=detail_language,
        book_detail_presentation=book_detail_presentation,
        # Isolated AST execution does not import module-level GTK helpers.
        # This scenario is Gallica-only; the Numilog scope is tested separately.
        _commercial_search_usage=lambda _item, _service=None: "",
        Adw=NS(PreferencesGroup=lambda: NS(set_title=lambda *_: None, add=lambda *_: None),
               ActionRow=lambda: NS(set_title=lambda *_: None)),
    )
    module = ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0), method], type_ignores=[]))
    exec(compile(module, str(path), "exec"), env)
    return env["show_search"]


def test_real_detail_method_shows_stale_warning_without_download_or_decoration():
    stamp = int(datetime(2026, 10, 16, 12, tzinfo=timezone.utc).timestamp())
    host = NS(
        window=NS(service=NS(content_type_for=lambda _: "ebook", provider_display_name=lambda _: "Gallica")),
        show_presentation=Mock(), _direct_download_action=Mock(), _decorate=Mock(),
        _append_content=Mock(),
    )
    render = _show_search()
    render(host, _stale(mirrored_at=stamp), loading=True)
    presentation = host.show_presentation.call_args.args[0]
    assert "Résultat connu · hors ligne" in presentation.badges
    fields = [(f.label, f.value) for section in presentation.sections for f in section.fields]
    assert ("Recherche en ligne", "Indisponible") in fields
    host._direct_download_action.assert_not_called()
    host._decorate.assert_not_called()
    host._append_content.assert_not_called()
    # Live public search keeps its existing explicit download/decorating path.
    render(host, _book("gallica"))
    host._direct_download_action.assert_called_once()
    host._decorate.assert_called_once()
