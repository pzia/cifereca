"""Real public provider adapters through the supervised local-origin broker."""
import importlib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from cifereca.core.local_origin_detail_broker import LocalOriginDetailReadFactory
from tests.test_core_p0_4030_public_catalogue import rig, rows, store


@pytest.mark.parametrize("provider,module,session_type,record,url,delivery,options", [
    ("gallica", "cifereca_gallica", "GallicaSession", "ark:/12148/bpt6k5493831p",
     "https://gallica.bnf.fr/ark:/12148/bpt6k5493831p",
     "https://gallica.bnf.fr/ark:/12148/bpt6k5493831p.epub", {"include_audio": False}),
    ("gutenberg", "cifereca_gutenberg", "GutenbergSession", "42",
     "https://www.gutenberg.org/ebooks/42",
     "https://www.gutenberg.org/ebooks/42.epub3.images", {"contact": "fixture@example.test"}),
])
def test_review_accepts_public_opds_source_without_following_delivery(
        rig, monkeypatch, provider, module, session_type, record, url, delivery, options):
    service, _, request, _, path = rig
    root = Path(__file__).resolve().parents[1] / "src" / "plugins"
    monkeypatch.syspath_prepend(str(root / ("provider-" + provider)))
    implementation = importlib.import_module(module)
    payload = f"""<feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>{url}</id><title>Roman commun</title><author><name>Autrice</name></author>
        <link rel="alternate" type="text/html" href="{url}"/>
        <link rel="http://opds-spec.org/acquisition" type="application/epub+zip" href="{delivery}"/>
      </entry>
    </feed>""".encode()
    requests = []

    def respond(incoming):
        requests.append(incoming)
        assert incoming.method == "GET"
        assert str(incoming.url) not in (url, delivery)
        assert any("Roman commun" in value for value in incoming.url.params.values())
        return httpx.Response(200, content=payload)

    monkeypatch.setattr(implementation, "HTTPX_TRANSPORT", SimpleNamespace(
        client=lambda **_options: httpx.Client(transport=httpx.MockTransport(respond)),
    ))
    adapter = getattr(implementation, session_type)
    session = adapter(credential_id="__public__", label=provider, credentials={},
                      options=options, config={}, diagnostics=None)
    try:
        session.connect()
        service._sessions = {"public": session}
        service._provider_enabled_for_identity = lambda p, i: (p, i) == (provider, "A")
        before, data = rows(service.storage), path.read_bytes()
        db = service.storage._connect()
        changes = db.total_changes
        broker = LocalOriginDetailReadFactory(service).for_activation("fixture", current=lambda: True)
        page = broker.preview(identity_id="A", placement_id=request.placement_id,
                              confirmed=True, still_current=lambda: True)
        choice, = page.public_choices
        assert choice.provider_id == provider
        assert requests == []
        assert broker.search_public(token=choice.token) is None
        assert requests == []
        report = broker.search_public(token=choice.token, confirmed=True)
        assert report.status == "partial" and report.provider_access_performed
        match, = report.matches
        assert match.provider_id == provider and match.record_id == record
        assert match.comparison == "title_author_concordant"
        assert not any((report.coverage_complete, report.fresh_network_verified,
                        report.association_authorized))
        assert broker.search_public(token=choice.token, confirmed=True) is None
        assert len(requests) == 1
        assert rows(service.storage) == before and path.read_bytes() == data
        assert db.total_changes == changes
    finally:
        session.close()
