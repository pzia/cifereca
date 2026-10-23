"""#1917: Gallica search capabilities describe independent public OPDS/SRU branches.

Pure in-memory fake branches: no Gallica HTTP, private account or download.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_gallica import DESCRIPTOR, GallicaSession


def fake_session(*, include_audio=True):
    session = object.__new__(GallicaSession)
    session.include_audio = include_audio
    session._diagnostic = lambda *args, **kwargs: None
    return session


def test_gallica_exposes_public_only_fields_and_branch_limits():
    cap = DESCRIPTOR.search_capabilities
    assert cap.declared
    assert cap.fields == ("all", "title", "author", "subject", "isbn")
    assert cap.scopes == ("all", "digital")
    assert cap.pagination == "page" and cap.page_size == 50
    assert cap.max_pages == 101
    assert any("partiels" in note for note in cap.notes)
    assert not cap.supports_scope("physical")


@pytest.mark.parametrize("field,scope", (
    ("language", "all"), ("", "all"), ("all", "physical"),
    ("isbn", "owned"), ("title", "private"),
))
def test_unsupported_search_is_rejected_before_any_branch(field, scope):
    session = fake_session()
    calls = []
    session._search_ebooks = lambda *args: calls.append("epub") or []
    session._search_audio = lambda *args: calls.append("audio") or []
    with pytest.raises(ProviderCapabilityError, match="unsupported_"):
        session.search("x", field=field, scope=scope)
    assert calls == []


@pytest.mark.parametrize("field", ("all", "title", "author", "subject", "isbn"))
def test_supported_field_routes_to_both_branches_without_inference(field):
    session = fake_session()
    calls = []
    session._search_ebooks = lambda *args: calls.append(("epub", args)) or []
    session._search_audio = lambda *args: calls.append(("audio", args)) or []
    assert session.search("Doyle", field=field, page=1, scope="digital") == []
    assert set(calls) == {
        ("epub", ("Doyle", 1, field)),
        ("audio", ("Doyle", 1, field)),
    }
    assert session.last_search_warnings == []


def test_branch_failure_preserves_healthy_results_and_public_warning():
    session = fake_session()
    row = NS(id="book-1", content_type="ebook")
    session._search_ebooks = lambda *args: [row]

    def unavailable(*_args):
        raise RuntimeError("PRIVATE_CREDENTIAL")

    session._search_audio = unavailable
    assert session.search("Doyle") == [row]
    assert len(session.last_search_warnings) == 1
    assert "audio" in session.last_search_warnings[0]
    assert "PRIVATE" not in str(session.last_search_warnings)


def test_both_branches_unavailable_is_not_misreported_as_empty_result():
    session = fake_session()
    session._search_ebooks = lambda *args: (_ for _ in ()).throw(RuntimeError("secret epub"))
    session._search_audio = lambda *args: (_ for _ in ()).throw(RuntimeError("secret audio"))
    with pytest.raises(ProviderError) as raised:
        session.search("Doyle")
    assert getattr(raised.value, "code", None) == "search_temporarily_unavailable"
    assert "secret" not in str(raised.value)


def test_unrequested_audio_branch_is_not_called():
    session = fake_session()
    session._search_ebooks = lambda *args: []
    session._search_audio = lambda *args: (_ for _ in ()).throw(AssertionError("should not run"))
    assert session.search("Doyle", field="title", content_types=("epub",)) == []
    assert session.last_search_warnings == []



def test_partial_search_diagnostics_redact_untrusted_exception_text():
    import httpx

    session = fake_session()
    events = []
    session._diagnostic = lambda *args, **kwargs: events.append((args, kwargs))
    row = NS(id="public-book", content_type="ebook")
    session._search_ebooks = lambda *args: [row]

    def fail_audio(*args):
        request = httpx.Request(
            "GET", "https://gallica.example/search?token=PRIVATE_SIGNED"
        )
        response = httpx.Response(429, request=request, text="Bearer PRIVATE_TOKEN")
        raise httpx.HTTPStatusError(
            "PRIVATE_SIGNED_URL Bearer PRIVATE_TOKEN",
            request=request, response=response,
        )

    session._search_audio = fail_audio
    assert session.search("public query") == [row]
    assert session.last_search_warnings == [
        "partial_results : Gallica : recherche audio temporairement indisponible"
    ]
    diagnostic = next(
        kwargs["detail"]
        for args, kwargs in events
        if args[:3] == ("GALLICA", "audio search branch", "error")
    )
    assert diagnostic["http_status"] == 429
    assert diagnostic["error"] == "HTTPStatusError"
    assert "message" not in diagnostic
    assert "PRIVATE" not in str(diagnostic)
    assert "token=" not in str(diagnostic)



@pytest.mark.parametrize("healthy,failed", (
    ("OPDS", "audio"),
    ("audio", "OPDS"),
))
def test_empty_successful_branch_remains_valid_zero_when_other_branch_fails(healthy, failed):
    """A healthy empty response is not a total service failure."""
    session = fake_session()
    calls = []

    def healthy_search(*_args):
        calls.append(healthy)
        return []

    def broken_search(*_args):
        calls.append(failed)
        raise RuntimeError("PRIVATE_SIGNED_URL?token=SECRET")

    session._search_ebooks = healthy_search if healthy == "OPDS" else broken_search
    session._search_audio = healthy_search if healthy == "audio" else broken_search

    assert session.search("absent", field="all") == []
    assert sorted(calls) == ["OPDS", "audio"]
    assert len(session.last_search_warnings) == 1
    assert session.last_search_warnings[0].startswith("partial_results : Gallica :")
    assert "PRIVATE_" not in session.last_search_warnings[0]


def test_only_failed_branch_still_raises_even_without_result_rows():
    """Audio-only search must not silently pass when SRU is unavailable."""
    session = fake_session()
    session._search_ebooks = lambda *_args: pytest.fail("unrequested OPDS branch")
    session._search_audio = lambda *_args: (_ for _ in ()).throw(RuntimeError("SECRET"))
    with pytest.raises(ProviderError) as raised:
        session.search("absent", content_types=("mp3",))
    assert getattr(raised.value, "code", None) == "search_temporarily_unavailable"
    assert "SECRET" not in str(raised.value)



@pytest.mark.parametrize("include_audio,formats", (
    (False, ("mp3",)),
    (True, ("video",)),
    (True, ("application/x-unknown",)),
))
def test_no_matching_media_branch_warns_instead_of_falsely_confirming_zero(
    include_audio, formats,
):
    session = fake_session(include_audio=include_audio)
    calls = []
    session._search_ebooks = lambda *_: calls.append("ebooks") or []
    session._search_audio = lambda *_: calls.append("audio") or []
    assert session.search("Doyle", content_types=formats) == []
    assert calls == []
    assert session.last_search_warnings == [
        "unsupported_content_type : aucune branche Gallica disponible pour les formats demandés"
    ]


def test_warning_for_unavailable_media_does_not_pollute_next_eligible_search():
    session = fake_session(include_audio=False)
    calls = []
    session._search_ebooks = lambda *_: calls.append("ebooks") or []
    session._search_audio = lambda *_: pytest.fail("audio disabled")
    assert session.search("Doyle", content_types=("mp3",)) == []
    assert len(session.last_search_warnings) == 1
    assert session.search("Doyle", content_types=("epub",)) == []
    assert calls == ["ebooks"]
    assert session.last_search_warnings == []


def test_explicit_mixed_formats_warn_when_configured_audio_is_skipped():
    session = fake_session(include_audio=False)
    calls = []
    book = NS(id="book-1", content_type="ebook")
    session._search_ebooks = lambda *_: calls.append("ebooks") or [book]
    session._search_audio = lambda *_: pytest.fail("audio disabled")
    assert session.search("Doyle", content_types=("epub", "mp3")) == [book]
    assert calls == ["ebooks"]
    assert session.last_search_warnings == [
        "partial_results : Gallica : branche audio désactivée par configuration"
    ]
    # The next explicitly ebook-only request is complete within its scope.
    assert session.search("Doyle", content_types=("epub",)) == [book]
    assert calls == ["ebooks", "ebooks"]
    assert session.last_search_warnings == []


def test_mixed_formats_with_enabled_audio_call_both_routes_without_warning():
    session = fake_session(include_audio=True)
    calls = []
    session._search_ebooks = lambda *_: calls.append("ebooks") or []
    session._search_audio = lambda *_: calls.append("audio") or []
    assert session.search("Doyle", content_types=("epub", "mp3")) == []
    assert set(calls) == {"ebooks", "audio"}
    assert session.last_search_warnings == []


def test_skipped_audio_does_not_turn_failed_ebook_branch_into_partial_success():
    session = fake_session(include_audio=False)
    session._search_ebooks = lambda *_: (_ for _ in ()).throw(RuntimeError("PRIVATE_TOKEN"))
    session._search_audio = lambda *_: pytest.fail("audio disabled")
    with pytest.raises(ProviderError, match="catalogue livres numériques temporairement indisponible"):
        session.search("Doyle", content_types=("epub", "mp3"))
    assert "PRIVATE_TOKEN" not in repr(session.last_search_warnings)


def test_disabled_audio_without_explicit_audio_request_has_no_false_warning():
    session = fake_session(include_audio=False)
    session._search_ebooks = lambda *_: []
    session._search_audio = lambda *_: pytest.fail("audio disabled")
    assert session.search("Doyle") == []
    assert session.last_search_warnings == []



@pytest.mark.parametrize("page", (-1, True, False, "2", "invalid", 0.5, None, 101, 10**100))
def test_invalid_page_refused_before_any_gallica_branch(page):
    session = fake_session()
    calls = []
    session._search_ebooks = lambda *_: calls.append("ebooks") or []
    session._search_audio = lambda *_: calls.append("audio") or []
    with pytest.raises(ProviderCapabilityError, match="invalid_page"):
        session.search("Doyle", page=page)
    assert calls == []
    assert session.last_search_warnings == []


@pytest.mark.parametrize("page", (0, 1, 100))
def test_valid_page_is_forwarded_unchanged_to_both_branches(page):
    session = fake_session()
    calls = []
    session._search_ebooks = lambda *_args: calls.append(("ebooks", _args)) or []
    session._search_audio = lambda *_args: calls.append(("audio", _args)) or []
    assert session.search("Doyle", page=page) == []
    assert set(calls) == {
        ("ebooks", ("Doyle", page, "all")),
        ("audio", ("Doyle", page, "all")),
    }
    assert session.last_search_warnings == []


@pytest.mark.parametrize('status,expected_code', (
    (401, "SearchAuthenticationRequired"),
    (403, "SearchAccessDenied"),
    (400, None),
    (404, None),
    (422, None),
    (429, "SearchTemporarilyUnavailable"),
    (503, "SearchTemporarilyUnavailable"),
))
def test_all_gallica_branches_failed_preserve_http_denial_or_semantic_status(
    status, expected_code,
):
    import httpx
    from cifereca.search_failures import search_failure_code
    from cifereca.core.known_catalog_fallback import public_service_unavailable
    session = fake_session()
    calls = []
    def failed_branch(label):
        def call(*args):
            calls.append(label)
            request = httpx.Request("GET", "https://gallica.bnf.fr/?token=PRIVATE_SECRET")
            response = httpx.Response(status, request=request, text="PRIVATE_BODY")
            raise httpx.HTTPStatusError("PRIVATE_URL PRIVATE_SECRET",
                                        request=request, response=response)
        return call
    session._search_ebooks = failed_branch("books")
    session._search_audio = failed_branch("audio")
    with pytest.raises(ProviderError) as caught:
        session.search("Doyle")
    assert sorted(calls) == ["audio", "books"]
    assert search_failure_code(caught.value) == expected_code
    assert public_service_unavailable(caught.value) is (expected_code == "SearchTemporarilyUnavailable")
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "PRIVATE" not in str(caught.value) + repr(session.last_search_warnings)


@pytest.mark.parametrize('status,expected_code', (
    (401, "SearchAuthenticationRequired"),
    (403, "SearchAccessDenied"),
))
def test_mixed_denial_and_timeout_never_enables_stale_fallback(status, expected_code):
    import httpx
    from cifereca.search_failures import search_failure_code
    from cifereca.core.known_catalog_fallback import public_service_unavailable
    session = fake_session()
    def denied(*args):
        req = httpx.Request("GET", "https://gallica.bnf.fr/?auth=PRIVATE_TOKEN")
        raise httpx.HTTPStatusError("PRIVATE", request=req,
              response=httpx.Response(status, request=req))
    def timeout(*args):
        raise httpx.ReadTimeout("PRIVATE_NET")
    session._search_ebooks = timeout
    session._search_audio = denied
    with pytest.raises(ProviderError) as caught:
        session.search("Doyle")
    assert search_failure_code(caught.value) == expected_code
    assert not public_service_unavailable(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_gallica_partial_branch_still_exposes_healthy_rows_despite_denial():
    import httpx
    session = fake_session()
    book = NS(id="attested", content_type="ebook")
    session._search_ebooks = lambda *_: [book]
    def denied(*_):
        request = httpx.Request("GET", "https://gallica.bnf.fr/PRIVATE?token=SECRET")
        raise httpx.HTTPStatusError("PRIVATE", request=request,
                                    response=httpx.Response(403, request=request))
    session._search_audio = denied
    assert session.search("Doyle") == [book]
    assert len(session.last_search_warnings) == 1
    assert session.last_search_warnings[0].startswith("partial_results")
    assert "PRIVATE" not in repr(session.last_search_warnings)
