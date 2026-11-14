"""P0 #3196: vendor-free search list hints never become delivery permissions."""
from __future__ import annotations

import ast
from pathlib import Path
from dataclasses import replace

from cifereca.gui.query_model import result_row
from tests.test_core_1337_kobo_personal_manifest_rights import _row


SEARCH = Path(__file__).resolve().parents[1] / "src/cifereca/gui/search_results.py"


def test_search_list_no_longer_promotes_kobo_specific_notice_text():
    source = SEARCH.read_text(encoding="utf-8")
    ast.parse(source)
    assert 'if item.source_provider == "kobo" and not stale_known:' not in source
    assert "Origine Kobo · téléchargement à vérifier auprès du fournisseur" not in source
    assert '_stale_catalog_provenance(item.metadata)' in source


def test_search_download_visibility_delegates_retired_direct_get_to_core():
    source = SEARCH.read_text(encoding="utf-8")
    assert '"kobo"' not in source
    assert 'item.source_provider == "numilog-catalog"' in source
    assert 'item.source_provider == "numilog"' not in source
    assert "private_notice_download_hint_allowed(" in source
    assert 'self.service.can_direct_download(item)' in source
    assert "if placement:" in source
    assert "elif direct:" in source


def test_unverified_kobo_owned_and_downloadable_catalogue_stays_inert():
    item = replace(_row(), downloadable=True, owned=True)
    projected = result_row(item)
    assert projected["state"] == "remote_catalog"
    assert projected["owned"] is False
