"""Offline, *public-only* reference catalogue plugin exported inside LLM ZIP.

These are literally the files to copy from the ZIP; generation is constant and
contains no private URL, capture value, local configuration or Core internals.
"""
from __future__ import annotations

import json

REFERENCE_MANIFEST = {
    "manifest": 1,
    "package": {
        "id": "org.cifereca.provider.fixture-example",
        "version": "0.1.0",
        "name": "Offline fixture catalogue",
        "publisher": "cifereca-example",
        "plugin_api": ">=1.4,<2",
    },
    "plugins": [{
        "id": "catalog.fixture-example",
        "type": "catalog.service",
        "version": "0.1.0",
        "entrypoint": "fixture_catalog:PLUGIN",
        "display_name": "Fixture catalogue public",
        "description": "Catalogue fictif, read-only, hors ligne ; aucun compte.",
        "enabled_by_default": False,
        "capabilities": ["catalog.search", "catalog.detail"],
        "requires": [],
        "recommends": [],
        "requires_contracts": [],
        "requires_brokers": [],
        "system_requires": [],
    }],
}

REFERENCE_CODE = '''"""Illustrative but *installable* Cifereca catalogue plugin (offline).

No HTTP client, tokens, identity, user rights or network calls exist.
This is a catalog.service dependency, not a provider.library or loan flow.
"""
from __future__ import annotations

from cifereca.plugin_api import (
    ItemDetail, NotFound, SearchResult, UnsupportedOperation,
)

# Explicitly fake data. NEVER replace by real account/session captures.
_ROWS = (
    ("book-214", "Fictional Atlas", "Example Author"),
    ("book-215", "Fictional Botany", "Example Author"),
    ("book-216", "Imaginary History", "Sample Writer"),
)


class PublicFixtureCatalog:
    """Proof of contract; does NOT attest acquisition, lending or ownership."""

    def __init__(self):
        self._cache: dict[tuple[str, str, int, int], tuple[SearchResult, ...]] = {}

    def search(self, query: str, *, field: str = "title",
               page: int = 1, limit: int = 20) -> list[SearchResult]:
        if field not in {"title", "author", "all"}:
            raise UnsupportedOperation("unsupported_field")
        if (type(query) is not str or len(query) > 100
                or type(page) is not int or not 1 <= page <= 1000
                or type(limit) is not int or not 1 <= limit <= 100):
            raise UnsupportedOperation("invalid_search")
        term = query.casefold().strip()
        key = (term, field, page, limit)
        if key not in self._cache:
            selected = []
            for book_id, title, author in _ROWS:
                text = {"title": title, "author": author,
                        "all": title + " " + author}[field]
                if term not in text.casefold():
                    continue
                selected.append(self._result(book_id, title, author))
            start = (page - 1) * limit
            # 32 bounded entries, no account identity and no cross-user data.
            if len(self._cache) >= 32:
                self._cache.clear()
            self._cache[key] = tuple(selected[start:start + limit])
        return list(self._cache[key])

    @staticmethod
    def _result(book_id: str, title: str, author: str) -> SearchResult:
        return SearchResult(
            id=book_id, record_id=book_id, docbase="fixture-public",
            medium="digital", title=title, author=author, type="book",
            format="epub", publisher=None, published_at=None,
            description=None, thumbnail_url=None, friendly_url=None,
            scenario="public_catalog", source_provider="fixture-example",
            source_credential="public", owned=None, downloadable=None,
            metadata={"fixture_only": True, "rights": "unverified"},
        )

    def detail(self, record_id: str) -> ItemDetail:
        for book_id, title, author in _ROWS:
            if book_id == record_id:
                return ItemDetail(
                    record_id=book_id, docbase="fixture-public", title=title,
                    author=author, type="book", year=None,
                    thumbnail_url=None, availability_status="unknown",
                    availability_label="Droits non vérifiés", holdings=[],
                    source_provider="fixture-example",
                    source_credential="public",
                )
        raise NotFound("fixture_record_not_found")


class CatalogPlugin:
    """Exactly the published cifereca.catalog.service/1 create Protocol."""

    def create(self, **_options) -> PublicFixtureCatalog:
        return PublicFixtureCatalog()


PLUGIN = CatalogPlugin()
'''

REFERENCE_TEST = '''"""Reference plugin has no network or account semantics."""
from __future__ import annotations

import pytest

from cifereca.plugin_api import (
    CatalogServiceFactory, ItemDetail, NotFound, SearchResult,
    UnsupportedOperation, check_service_contract,
)
from fixture_catalog import PLUGIN


def test_contract_matches_running_core():
    assert isinstance(PLUGIN, CatalogServiceFactory)
    assert check_service_contract("catalog.service", PLUGIN).ok


def test_search_pagination_and_detail_fictional():
    service = PLUGIN.create()
    rows = service.search("Fictional", field="title", limit=1, page=2)
    assert len(rows) == 1
    assert isinstance(rows[0], SearchResult)
    assert rows[0].record_id == "book-215"
    assert rows[0].owned is None
    assert rows[0].downloadable is None
    assert rows[0].metadata["rights"] == "unverified"
    assert isinstance(service.detail("book-214"), ItemDetail)
    assert service.detail("book-214").availability_status == "unknown"
    assert service.search("fictional", page=3, limit=1) == []


def test_denied_ops_and_no_auth_guess():
    service = PLUGIN.create()
    with pytest.raises(UnsupportedOperation):
        service.search("test", field="borrow")
    with pytest.raises(UnsupportedOperation):
        service.search("test", page=0)
    with pytest.raises(NotFound):
        service.detail("unknown")
    assert not hasattr(service, "borrow")
    assert not hasattr(service, "download")
    assert not hasattr(service, "authenticate")
'''

REFERENCE_README = r"""# Provider fixture catalogue — exemple exécutable et isolé

**Non normatif pour les vraies API fournisseur.** Le registre public
\`plugin-dev-kit/contracts/\` détermine la compatibilité.

Copier ce dossier seul vers \`src/plugins/provider-fixture-example/\`.
L'exemple expose le contrat officiel \`catalog.service\` via
\`fixture_catalog:PLUGIN\`, \`create()\`, \`SearchResult\` et \`ItemDetail\`.
Il implémente recherche, détail, pagination, cache borné et erreurs typées,
sur **trois notices inventées**. Aucun accès HTTP, identifiant, prêt, ACSM,
DRM, achat, possession ni téléchargement. Capacités \`catalog.search\`
et \`catalog.detail\` sont uniquement attestées par l'exemple local.

Depuis le dépôt Cifereca cible et avec \`cifereca.plugin_api\` installé :

\`\`\`sh
python -m pytest -q src/plugins/provider-fixture-example/tests
python scripts/verify-packaging.py
python scripts/package-plugin.py src/plugins/provider-fixture-example
\`\`\`

Les commandes de packaging peuvent varier selon la version cible :
vérifier l'aide du script officiel de cette installation ; ne jamais
inventer de flags supplémentaires. L'exemple n'est **pas un provider
library**, ni une preuve de droits, ni un transport réseau utilisateur.
Pour un fournisseur réel, requalifier d'abord chaque endpoint, identités,
TLS, anti-SSRF, type de catalogue et permissions à partir de la trace.
"""


def reference_plugin_files() -> dict[str, bytes]:
    root = "plugin-dev-kit/examples/provider-fixture-example/"
    return {
        root + "cifereca-plugin.json": (
            json.dumps(REFERENCE_MANIFEST, indent=2, ensure_ascii=False)
            + "\n"
        ).encode("utf-8"),
        root + "fixture_catalog.py": REFERENCE_CODE.encode("utf-8"),
        root + "tests/test_fixture_catalog.py": REFERENCE_TEST.encode("utf-8"),
        root + "README.md": REFERENCE_README.encode("utf-8"),
    }
