"""#4030: real EbooksGratuits OPDS adapter through the local-origin broker.

The HTTP transport and the EPUB are synthetic. A public catalogue hit is
descriptive evidence only: no delivery GET, OriginLink or right is created.
"""
import hashlib
import importlib
from pathlib import Path

import httpx

from cifereca.core.local_origin_detail_broker import LocalOriginDetailReadFactory
from tests.test_core_p0_4030_public_catalogue import rig, rows, store


_ROOT = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <link rel="search" type="application/opensearchdescription+xml"
        href="/index.php/opensearch.xml"/>
</feed>"""

_OPENSEARCH = """<?xml version="1.0"?>
<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
  <Url type="application/atom+xml;profile=opds-catalog"
       template="https://opds2.ebooksgratuits.org/index.php/feed/search?q={searchTerms}"/>
</OpenSearchDescription>"""

_SEARCH = """<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:42</id>
    <title>Roman commun</title>
    <author><name>Autrice</name></author>
    <link rel="alternate" type="text/html" href="/index.php/book/42"/>
    <link rel="http://opds-spec.org/acquisition/open-access"
          type="application/epub+zip" href="/index.php/fetch/42.epub"/>
  </entry>
</feed>"""


def test_ebooksgratuits_public_source_is_an_advisory_hit_not_a_delivery(
        rig, monkeypatch):
    service, _, request, _, local_path = rig
    plugins = Path(__file__).resolve().parents[1] / "src" / "plugins"
    monkeypatch.syspath_prepend(str(plugins / "ebooksgratuits"))
    provider = importlib.import_module("cifereca_ebooksgratuits")

    routes = {
        "/index.php/feed": _ROOT,
        "/index.php/opensearch.xml": _OPENSEARCH,
        "/index.php/feed/search": _SEARCH,
    }
    requests = []

    def respond(incoming):
        requests.append(incoming)
        assert incoming.method == "GET"
        assert incoming.url.host == "opds2.ebooksgratuits.org"
        assert incoming.url.path in routes  # A book page or EPUB GET is forbidden.
        if incoming.url.path == "/index.php/feed/search":
            assert incoming.url.params["q"] == "Roman commun"
        return httpx.Response(200, text=routes[incoming.url.path], request=incoming)

    session = provider.EbooksSession(
        credential_id="__public__", label="EbooksGratuits", credentials={},
        options={}, config={}, diagnostics=None, transport=None,
    )
    try:
        session.client.close()
        session.client = httpx.Client(
            transport=httpx.MockTransport(respond), trust_env=False,
        )
        session.connect()
        service._sessions = {"public": session}
        service._provider_enabled_for_identity = lambda p, i: (
            p, i) == ("ebooksgratuits", "A")
        before, content = rows(service.storage), local_path.read_bytes()
        db = service.storage._connect()
        mutations = db.total_changes

        broker = LocalOriginDetailReadFactory(service).for_activation(
            "fixture", current=lambda: True,
        )
        page = broker.preview(
            identity_id="A", placement_id=request.placement_id,
            confirmed=True, still_current=lambda: True,
        )
        choice, = page.public_choices
        assert choice.provider_id == "ebooksgratuits" and requests == []
        assert broker.search_public(token=choice.token) is None
        assert requests == []

        result = broker.search_public(token=choice.token, confirmed=True)
        assert result.status == "partial" and result.provider_access_performed
        hit, = result.matches
        expected_id = hashlib.sha256(
            (provider.OPDS_FEED + "|urn:uuid:42").encode("utf-8")
        ).hexdigest()[:24]
        assert (hit.provider_id, hit.record_id, hit.docbase) == (
            "ebooksgratuits", expected_id, "EBOOKSGRATUITS",
        )
        assert (hit.title, hit.author, hit.comparison) == (
            "Roman commun", "Autrice", "title_author_concordant",
        )
        assert (not result.coverage_complete
                and not result.fresh_network_verified
                and not result.association_authorized)
        assert broker.search_public(token=choice.token, confirmed=True) is None
        assert [r.url.path for r in requests] == [
            "/index.php/feed", "/index.php/opensearch.xml",
            "/index.php/feed/search",
        ]
        assert rows(service.storage) == before
        assert local_path.read_bytes() == content
        assert db.total_changes == mutations
    finally:
        session.close()
