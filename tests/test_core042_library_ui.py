from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from types import SimpleNamespace

from cifereca.config import Config
from cifereca.core.search import filter_loaded_results
from cifereca.diagnostics import Diagnostics
from cifereca.gui.book_facets import facets_for_book
from cifereca.plugin_api import ArtifactFormat, ArtifactRef, ArtifactRouteRequest, PLUGIN_API_VERSION, MANIFEST_VERSION, ResolvedPluginService, StorageTarget
from cifereca.artifact_runtime import ArtifactExecutionBrokerImpl, ArtifactRoutingBrokerImpl
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _service(tmp_path: Path) -> LibraryService:
    config = Config(tmp_path / 'config.json')
    config.set('library.directory', str(tmp_path / 'library'))
    config.set('identities', [{'id': 'alice', 'label': 'Alice'}])
    return LibraryService(config, Storage(tmp_path / 'db.sqlite3'), Diagnostics())


def test_library_projection_deduplicates_same_sha256_across_sources(tmp_path):
    service = _service(tmp_path)
    try:
        path = tmp_path / 'library' / 'Book.epub'; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'same-book')
        sha = hashlib.sha256(path.read_bytes()).hexdigest(); aid = f'sha256:{sha}'
        service.storage.artifact_put({'artifact_id': aid, 'sha256': sha, 'size': path.stat().st_size, 'media_format': 'epub', 'mime_type': 'application/epub+zip', 'metadata': {}})
        p1 = service.storage.placement_put({'artifact_id': aid, 'path': str(path), 'identity_id': 'alice', 'provider_id': 'paris', 'source_item_id': 'p-1', 'state': 'present', 'metadata': {}})
        service.storage.catalog_entry_put({'artifact_id': aid, 'placement_id': p1, 'title': 'Book', 'author': 'Author', 'media_format': 'epub', 'identity_id': 'alice', 'provider_id': 'paris', 'item_id': 'p-1', 'local_state': 'local', 'metadata': {'path': str(path)}})
        second = tmp_path / 'library' / 'Book copy.epub'; shutil.copy2(path, second)
        p2 = service.storage.placement_put({'artifact_id': aid, 'path': str(second), 'identity_id': 'alice', 'provider_id': 'kobo', 'source_item_id': 'k-1', 'state': 'present', 'metadata': {}})
        service.storage.catalog_entry_put({'artifact_id': aid, 'placement_id': p2, 'title': 'Book', 'author': 'Author', 'media_format': 'epub', 'identity_id': 'alice', 'provider_id': 'kobo', 'item_id': 'k-1', 'local_state': 'local', 'metadata': {'path': str(second)}})
        rows = service.library_entries(identity_id='alice')
        assert len(rows) == 1
        row = rows[0]
        assert row['artifact_id'] == aid and row['sha256'] == sha
        assert set(row['providers']) == {'paris', 'kobo'}
        assert row['placement_count'] == 2
        assert {p['placement_id'] for p in row['placements']} == {p1, p2}
    finally:
        service.close()


def test_language_filter_normalizes_aliases():
    rows = [
        {'key': 'fr', 'metadata': {'languages': ['fre']}},
        {'key': 'en', 'metadata': {'languages': ['ENG']}},
        {'key': 'none', 'metadata': {}},
    ]
    assert [r['key'] for r in filter_loaded_results(rows, languages=('fr',))] == ['fr']
    assert [r['key'] for r in filter_loaded_results(rows, languages=('en',))] == ['en']


def test_list_facets_can_represent_multiple_sources():
    facets = facets_for_book({'providers': ['paris', 'kobo'], 'author': 'A', 'media_format': 'epub', 'metadata': {'collection': 'C'}})
    sources = {(f.field, f.value) for f in facets if f.field == 'source'}
    assert sources == {('source', 'paris'), ('source', 'kobo')}


class _TargetService:
    def __init__(self, root: Path): self.root = root
    def configured_targets(self): return (StorageTarget('reader', self.root, 'Reader'),)
    def matches(self, target): return True


class _Manager:
    def __init__(self, target): self.target = target
    def services(self, category):
        if category != 'storage.target': return ()
        return (ResolvedPluginService('storage.test', '1', 'pkg', '1', 'storage.target', self.target, 1, 'cifereca.storage', '1', (), 3),)


def test_artifact_routing_hides_target_already_holding_same_artifact(tmp_path):
    storage = Storage(tmp_path / 'db.sqlite3')
    source = tmp_path / 'Book.epub'; source.write_bytes(b'book')
    sha = hashlib.sha256(source.read_bytes()).hexdigest(); aid = f'sha256:{sha}'
    storage.artifact_put({'artifact_id': aid, 'sha256': sha, 'size': source.stat().st_size, 'media_format': 'epub', 'mime_type': 'application/epub+zip', 'metadata': {}})
    storage.placement_put({'artifact_id': aid, 'path': str(source), 'identity_id': 'alice', 'provider_id': 'local', 'state': 'present', 'metadata': {}})
    target_dir = tmp_path / 'reader'; target_dir.mkdir(); copied = target_dir / 'Book.epub'; shutil.copy2(source, copied)
    storage.placement_put({'artifact_id': aid, 'path': str(copied), 'identity_id': 'alice', 'provider_id': 'storage', 'state': 'present', 'metadata': {'placement_kind': 'storage', 'storage_target_id': 'reader', 'storage_plugin_id': 'storage.test'}})
    service = SimpleNamespace(storage=storage, plugin_manager=_Manager(_TargetService(target_dir)))
    artifact = ArtifactRef(aid, 'book', ArtifactFormat('application/epub+zip', 'epub'), source.stat().st_size, sha, filename=source.name)
    routing = ArtifactRoutingBrokerImpl(service, ArtifactExecutionBrokerImpl(service))
    assert routing.targets(ArtifactRouteRequest(artifact, identity_id='alice')) == ()
    assert len(routing.targets(ArtifactRouteRequest(artifact, identity_id='alice', options={'include_existing': True}))) == 1


def test_core042_keeps_public_contract_version_stable():
    assert PLUGIN_API_VERSION == '1.4.3'
    assert MANIFEST_VERSION == 1
