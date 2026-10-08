from __future__ import annotations

import pytest

from cifereca.plugin_api import ProviderError
from cifereca_gutenberg import GutenbergSession


class FailingDiagnostics:
    def add(self, *_args, **_kwargs):
        raise RuntimeError("diagnostics unavailable")


def _session():
    return GutenbergSession(
        credential_id="public",
        label="",
        credentials={},
        options={"contact": "tests@example.test"},
        config={},
        diagnostics=FailingDiagnostics(),
    )


def test_gutenberg_valid_parse_survives_diagnostics_failure():
    xml = b'''<feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>https://www.gutenberg.org/ebooks/123</id>
        <title>Example</title>
        <link rel="http://opds-spec.org/acquisition" type="application/epub+zip" href="/ebooks/123.epub"/>
      </entry>
    </feed>'''
    session = _session()
    try:
        rows = session._parse_feed(xml, "https://www.gutenberg.org/ebooks/search.opds/")
        assert [row.record_id for row in rows] == ["123"]
    finally:
        session.close()


def test_gutenberg_parse_error_is_not_replaced_by_diagnostics_failure():
    session = _session()
    try:
        with pytest.raises(ProviderError, match="XML invalide"):
            session._parse_feed(b"<feed>", "https://www.gutenberg.org/ebooks/search.opds/")
    finally:
        session.close()
