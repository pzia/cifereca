from __future__ import annotations

from io import BytesIO
import zipfile

import httpx

from cifereca_numilog import NumilogSession


class ExplodingDiagnostics:
    def add(self, *_args, **_kwargs):
        raise RuntimeError("diagnostics unavailable")


def _session() -> NumilogSession:
    return NumilogSession(
        credential_id="numilog-test",
        label="Numilog",
        credentials={"email": "reader@example.test", "password": "secret"},
        options={},
        config={},
        diagnostics=ExplodingDiagnostics(),
    )


def _epub_bytes() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        )
        archive.writestr("OEBPS/content.opf", "<package/>")
    return buffer.getvalue()


def test_http_success_is_not_changed_by_diagnostics_failure():
    session = _session()
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="ok", request=request)
        ),
        follow_redirects=False,
        trust_env=False,
    )
    try:
        url, text = session._page(session.base_url)

        assert url == session.base_url
        assert text == "ok"
    finally:
        session.close()


def test_library_success_is_not_changed_by_diagnostics_failure():
    session = _session()
    session.connected = True
    session._rows = lambda: [{
        "lid": "12",
        "variant": "()",
        "title": "Livre",
        "cover": None,
        "href": session.library_url + "?lid=12",
        "public_url": None,
    }]
    try:
        rows = session.library()

        assert len(rows) == 1
        assert rows[0].title == "Livre"
    finally:
        session.close()


def test_epub_download_success_is_not_changed_by_diagnostics_failure(tmp_path):
    payload = _epub_bytes()
    session = _session()
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={
                    "content-type": "application/epub+zip",
                    "content-length": str(len(payload)),
                },
                content=payload,
                request=request,
            )
        ),
        follow_redirects=False,
        trust_env=False,
    )
    try:
        destination = tmp_path / "book.epub"
        produced, source = session._download(
            session.base_url + "download/book.epub",
            destination,
        )

        assert produced == destination
        assert destination.is_file()
        assert zipfile.is_zipfile(destination)
        assert source == session.library_url
    finally:
        session.close()
