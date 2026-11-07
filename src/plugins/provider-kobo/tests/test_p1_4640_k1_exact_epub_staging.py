"""#4640 K1: selected EPUB over PDF must survive preflight-to-staging.

The real Kobo provider session is driven by synthetic Vault descriptors and
a fake Core HTTPX transport. No external Kobo request or DRM runtime.
"""
from copy import deepcopy

import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p1_3252_kobo_selected_delivery_offer import (
    SIGNED, descriptor, setup,
)
from tests.test_p1_3252_kobo_selected_candidate import (
    admitted, execute, epub_bytes, fake_public_client,
)


PDF_URL = "https://cdn.kobo.com/alternative/book.pdf?signature=PRIVATE_PDF"


def _epub_and_pdf(*, pdf_first=False):
    envelope = descriptor()
    epub = envelope["acquisition"]["candidates"][0]
    pdf = deepcopy(epub)
    pdf["media_format"] = "pdf"
    pdf["url"] = PDF_URL
    envelope["acquisition"]["candidates"] = (
        [pdf, epub] if pdf_first else [epub, pdf]
    )
    return envelope


@pytest.mark.parametrize("preflight_pdf_first", [False, True])
@pytest.mark.parametrize("fresh_pdf_first", [False, True])
def test_k1_executes_only_unique_epub_independently_of_both_orders(
    tmp_path, monkeypatch, preflight_pdf_first, fresh_pdf_first,
):
    session, vault, _broker, account, posts = setup()
    descriptor_reads = []
    public_gets = []
    body = epub_bytes()

    def upstream(product, **options):
        posts.append((product, options))
        assert product == "book-1" and options.get("mode") == "descriptor"
        first = len(descriptor_reads) == 0
        descriptor_reads.append(options)
        return _epub_and_pdf(pdf_first=(
            preflight_pdf_first if first else fresh_pdf_first
        ))

    vault.acquisition = upstream

    def handler(request):
        public_gets.append((request.method, str(request.url)))
        assert request.method == "GET" and str(request.url) == SIGNED
        return httpx.Response(
            200, content=body,
            headers={"content-type": "application/epub+zip"},
        )

    fake_public_client(monkeypatch, handler, [])
    try:
        offered = admitted(session, account)
        assert offered.startswith("kobo.descriptor:")
        assert public_gets == [] and len(descriptor_reads) == 1
        staged, origin = execute(session, account, offered, tmp_path)
        assert staged.suffix == ".epub" and staged.read_bytes() == body
        assert origin == ""
        assert public_gets == [("GET", SIGNED)]
        assert len(descriptor_reads) == 2
        assert len(posts) == 2
        assert PDF_URL not in str(public_gets)
    finally:
        session.close()


@pytest.mark.parametrize("reverse", [False, True])
def test_k1_two_distinct_epubs_never_choose_first_or_get(
    tmp_path, monkeypatch, reverse,
):
    session, vault, _broker, account, posts = setup()
    envelope = descriptor()
    first = envelope["acquisition"]["candidates"][0]
    second = deepcopy(first)
    second["url"] = "https://cdn.kobo.com/another.epub?signature=PRIVATE_SECOND"
    envelope["acquisition"]["candidates"] = (
        [second, first] if reverse else [first, second]
    )
    vault.acquisition = lambda product, **options: (
        posts.append((product, options)) or deepcopy(envelope)
    )
    fake_public_client(
        monkeypatch, lambda request: pytest.fail("No ambiguous EPUB GET"), [],
    )
    try:
        snapshot = session.rights_delivery_offer_snapshot(
            account, "book-1", explicit_user_action=True,
        )
        assert snapshot.rights[0].delivery == "unknown"
        assert snapshot.rights[0].delivery_offer_id == ""
        assert len(posts) == 1 and posts[0][1]["mode"] == "descriptor"
        assert not list(tmp_path.iterdir())
    finally:
        session.close()


def test_k1_fresh_descriptor_drops_epub_and_never_executes_pdf(
    tmp_path, monkeypatch,
):
    session, vault, _broker, account, posts = setup()
    requests = []
    fake_public_client(
        monkeypatch, lambda request: (
            requests.append(request) or
            pytest.fail("Fresh PDF cannot replace selected EPUB")
        ), [],
    )
    def original_k1(product, **options):
        posts.append((product, options))
        return _epub_and_pdf()

    vault.acquisition = original_k1
    try:
        offered = admitted(session, account)
        assert offered.startswith("kobo.descriptor:")

        def only_pdf(product, **options):
            posts.append((product, options))
            row = _epub_and_pdf()["acquisition"]["candidates"][1]
            return descriptor(candidate=row)

        vault.acquisition = only_pdf
        with pytest.raises(ProviderCapabilityError):
            execute(session, account, offered, tmp_path)
        assert requests == [] and not list(tmp_path.iterdir())
        assert len(posts) == 2
    finally:
        session.close()
