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
