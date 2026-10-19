"""#1917: short Gallica query cannot disguise unsupported media as catalogue zero.

No HTTP or private search. Both Gallica branches are substituted in memory.
"""
import pytest
from cifereca_gallica import GallicaSession

WARNING = (
    "unsupported_content_type : aucune branche Gallica disponible "
    "pour les formats demandés"
)


def fake(*, audio=True):
    obj = object.__new__(GallicaSession)
    obj.include_audio = audio
    obj._diagnostic = lambda *_args, **_kwargs: None
    calls = []
    obj._search_ebooks = lambda *_a: calls.append("ebooks") or []
    obj._search_audio = lambda *_a: calls.append("audio") or []
    return obj, calls


@pytest.mark.parametrize(("audio", "formats"), [
    (False, ("mp3",)),
    (False, ("ogg",)),
    (True, ("video",)),
    (True, ("application/x-unknown",)),
])
@pytest.mark.parametrize("query", ["", "x", None])
def test_short_query_without_active_branch_is_not_silent_zero(audio, formats, query):
    obj, calls = fake(audio=audio)
    assert obj.search(query, content_types=formats) == []
    assert obj.last_search_warnings == [WARNING]
    assert calls == []


@pytest.mark.parametrize("formats", [
    (), ("epub",), ("mp3",), ("epub", "mp3"),
])
def test_short_query_with_available_branch_keeps_noop_behavior(formats):
    obj, calls = fake()
    obj.last_search_warnings = ["stale"]
    assert obj.search("x", content_types=formats) == []
    assert obj.last_search_warnings == []
    assert calls == []


def test_long_query_still_searches_only_available_branch():
    obj, calls = fake(audio=False)
    assert obj.search("Doyle", content_types=("epub", "mp3")) == []
    assert calls == ["ebooks"]
    assert obj.last_search_warnings == [
        "partial_results : Gallica : branche audio désactivée par configuration"
    ]
