"""#1917: Gallica mixed media requests disclose unavailable format coverage."""
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_gallica import GallicaSession


def session(*, audio=True):
    obj = object.__new__(GallicaSession)
    obj.include_audio = audio
    obj._diagnostic = lambda *args, **kwargs: None
    return obj


def test_unknown_format_in_mixed_ebook_request_warns_without_extra_http():
    s = session()
    calls = []
    book = NS(id="ebook", content_type="ebook")
    s._search_ebooks = lambda *args: calls.append("ebook") or [book]
    s._search_audio = lambda *args: pytest.fail("audio not requested")
    assert s.search("Doyle", content_types=("epub", "application/x-private")) == [book]
    assert calls == ["ebook"]
    assert s.last_search_warnings == [
        "partial_results : Gallica : certains formats demandés ne sont pas pris en charge"
    ]
    assert s.search("Doyle", content_types=("epub",)) == [book]
    assert s.last_search_warnings == []


def test_unknown_format_does_not_hide_disabled_audio_warning():
    s = session(audio=False)
    s._search_ebooks = lambda *args: [NS(id="ebook", content_type="ebook")]
    s._search_audio = lambda *args: pytest.fail("audio disabled")
    assert len(s.search("Doyle", content_types=("epub", "mp3", "video"))) == 1
    assert "branche audio" in s.last_search_warnings[0]
    assert "certains formats" in s.last_search_warnings[1]


@pytest.mark.parametrize("invalid", [
    None, "epub", {"epub": 1}, (None,), ("epub", object()), ("x" * 81,),
])
def test_invalid_content_type_input_is_typed_before_any_search(invalid):
    s = session()
    calls = []
    s._search_ebooks = lambda *args: calls.append("ebook")
    s._search_audio = lambda *args: calls.append("audio")
    with pytest.raises(ProviderCapabilityError, match="unsupported_content_type"):
        s.search("Doyle", content_types=invalid)
    assert calls == []
    assert s.last_search_warnings == []


def test_all_unknown_formats_keep_existing_no_branch_warning():
    s = session()
    s._search_ebooks = lambda *args: pytest.fail("not requested")
    s._search_audio = lambda *args: pytest.fail("not requested")
    assert s.search("Doyle", content_types=("video",)) == []
    assert s.last_search_warnings == [
        "unsupported_content_type : aucune branche Gallica disponible pour les formats demandés"
    ]
