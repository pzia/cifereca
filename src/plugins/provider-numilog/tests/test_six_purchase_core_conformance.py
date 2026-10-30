"""Actual Numilog adapter -> existing Core Sync, with six synthetic purchases.

Real HTML/HTTPX, broker, SQLite, files, consent and publication. No live
supplier, ADEPT, new reconciliation engine or production data is involved.
"""
import hashlib
import io
from pathlib import Path
import zipfile

import httpx
import pytest

from cifereca.brokers import IdentityBrokerImpl
from cifereca.catalog import LocalCatalog
from cifereca.config import Config
from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.source_convergence import SourceKey
from cifereca.diagnostics import Diagnostics
from cifereca.plugin_api import Account
from cifereca.services import LibraryService
from cifereca.storage import Storage
from cifereca_numilog import NumilogSession
from tests.test_numilog_owned_js_delivery_p0 import _card, _page
from tests.test_numilog_not_specified_epub_confirmation_p0_2024 import SIGNED_EPUB


def epub_bytes(index):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in {
            "mimetype": "application/epub+zip",
            "META-INF/container.xml": (
                '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                'media-type="application/oebps-package+xml"/></rootfiles></container>'
            ),
            "OEBPS/content.opf": (
                '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
                'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                f'<dc:title>Synthetic purchase {index}</dc:title><dc:identifier id="id">'
                f'urn:synthetic:{index}</dc:identifier><dc:language>fr</dc:language>'
                '</metadata><manifest><item id="text" href="text.xhtml" '
                'media-type="application/xhtml+xml"/></manifest>'
                '<spine><itemref idref="text"/></spine></package>'
            ),
            "OEBPS/text.xhtml": (
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                f'<p>Synthetic purchase {index}</p></body></html>'
            ),
        }.items():
            archive.writestr(zipfile.ZipInfo(name, (2026, 10, 29, 0, 0, 0)), data)
    return stream.getvalue()


@pytest.fixture
def purchases(tmp_path, monkeypatch):
    config = Config(tmp_path / "config.json")
    root = tmp_path / "EBooks"
    config.set("library.directory", str(root))
    config.set("identities", [
        {"id": "default", "label": "Hugues", "directory_name": "hugues"},
        {"id": "alex", "label": "Alexandre", "directory_name": "alex"},
    ])
    config.set("providers.accounts", [])
    store = Storage(tmp_path / "state.sqlite")
    service = LibraryService(config, store, Diagnostics())
    session = NumilogSession(
        credential_id="synthetic-six", label="Numilog synthetic", credentials={},
        options={}, config={}, diagnostics=None,
        identity_broker=IdentityBrokerImpl(service),
    )
    account = Account(identity_id="default", provider_id="numilog",
                      credential_id=session.credential_id, unique_id="numilog:" + session.scope)
    session._accounts = [account]
    session.connected = True
    service.accounts = [account]
    service._sessions[service._session_key("numilog", session.credential_id)] = session
    connection = store.provider_connection_put(dict(
        identity_id=account.identity_id, provider_id="numilog",
        credential_id=account.credential_id, label="Synthetic",
    ))
    store.provider_account_put(dict(
        identity_id=account.identity_id, provider_id="numilog",
        credential_id=account.credential_id, connection_id=connection,
        account_id=account.unique_id, label="Synthetic",
    ))
    payloads = [epub_bytes(i) for i in range(6)]
    urls = [SIGNED_EPUB.replace("productid=9001", f"productid={9001 + i}")
            for i in range(6)]
    cards = [_card(11 * (i + 1), 9001 + i, urls[i],
                   attrs=f'onclick="ModalDownload({9001 + i}, 1)"').replace(
                       "DrmTechnlology: 'ADOBE', link:",
                       "DrmTechnlology: 'NOT_SPECIFIED', format: 'EPUB', link:",
                   ) for i in range(6)]
    html = _page(*cards)
    gets = []

    def handler(request):
        url = str(request.url)
        gets.append(url)
        # Change only the UI at each private response. Neither the session
        # owner nor Core's source generation may follow that selector.
        service.select_identity("alex" if service.current_identity.identity_id == "default"
                                else "default")
        if url == session.library_url:
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        assert url in urls, "No public enrichment or unselected delivery request"
        return httpx.Response(200, content=payloads[urls.index(url)],
                              headers={"content-type": "application/epub+zip"})

    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    lock = tmp_path / "locks" / "acquisition.lock"
    for module in ("sync_coordination", "canonical_selected_offers",
                   "canonical_selected_delivery", "convergence_execution", "canonical_recovery"):
        monkeypatch.setattr("cifereca.core." + module + ".acquisition_lock_file", lambda: lock)
    monkeypatch.setattr("cifereca.services.acquisition_lock_file", lambda: lock)
    try:
        snapshot = session.rights_observation_snapshot(account)
        assert len(snapshot.rights) == 6
        keys = [SourceKey(snapshot.scope, r.edition_id, r.right_id, r.kind, snapshot.branch)
                for r in snapshot.rights]
        persist_attested_source_references(store, snapshot)
        paths = []
        for i, directory in enumerate(("numilog", "numilog", "numilog",
                                       "numilog/acsm-one", "numilog/acsm-two/deep",
                                       "numilog/other/nested")):
            path = root / "hugues" / directory / f"local-{i}.epub"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payloads[i])
            paths.append(path)
        yield service, session, account, snapshot, keys, paths, urls, gets, root
    finally:
        service.close()


def attest(service, snapshot, key, path, root):
    publish_completed_acquisition(
        service.storage, library_root=root, path=path, source=key, snapshot=snapshot,
        final_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        delivery_receipt_ref="synthetic-exact-" + key.right_id,
    )


def content_gets(gets, urls):
    return [url for url in gets if url in urls]


@pytest.fixture(params=("individual", "global"))
def synchronize(request):
    def sync(service, account):
        if request.param == "individual":
            result = service.synchronize_account_source(account)
        else:
            summary = service.opening_sync()
            result, = [row for row in summary["identities"]
                       if row["identity_id"] == account.identity_id]
            assert result["errors"] == 0
        return result["canonical_execution"]
    return sync


def test_six_exact_numilog_origins_skip_delivery_in_root_and_nested_paths(purchases, synchronize):
    service, session, account, snapshot, keys, paths, urls, gets, root = purchases
    for key, path in zip(keys, paths):
        attest(service, snapshot, key, path, root)
    before = {path: path.read_bytes() for path in paths}
    placements = service.storage.placements()
    for _ in range(2):
        counts = synchronize(service, account)
        assert counts["present_attested"] == 6
        assert counts["downloaded"] == counts["failed"] == counts["blocked"] == counts["pending"] == 0
        assert content_gets(gets, urls) == []
        assert service.storage.placements() == placements
        assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize("consent", [False, True])
def test_three_local_files_without_provider_origin_do_not_veto_selected_delivery(purchases, synchronize, consent):
    service, session, account, snapshot, keys, paths, urls, gets, root = purchases
    for i, (key, path) in enumerate(zip(keys, paths)):
        if i < 3:
            attest(service, snapshot, key, path, root)
        result = LocalCatalog(service.storage, root).index_path(path, identity_id="default")
        assert result["status"] == "indexed"
    before = {path: path.read_bytes() for path in paths}
    if consent:
        for key in keys[3:]:
            service.set_canonical_acquisition_consent(account, key, allowed=True)
    counts = synchronize(service, account)
    assert counts["present_attested"] == 3
    assert counts["failed"] == counts["blocked"] == 0
    assert counts["downloaded"] == (3 if consent else 0)
    assert counts["pending"] == (0 if consent else 3)
    assert content_gets(gets, urls) == (urls[3:] if consent else [])
    assert {path: path.read_bytes() for path in paths} == before
    assert len(service.storage.artifacts()) == 6
    assert len(service.storage.placements()) == (9 if consent else 6)
    with service.storage._lock, service.storage._connect() as db:
        assert db.execute("SELECT count(*) FROM origin_links").fetchone()[0] == (6 if consent else 3)
    again = synchronize(service, account)
    assert again["present_attested"] == (6 if consent else 3)
    assert again["downloaded"] == again["failed"] == again["blocked"] == 0
    assert content_gets(gets, urls) == (urls[3:] if consent else [])
