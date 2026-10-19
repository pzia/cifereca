"""#1917: Gallica rejects malformed public search inputs before branch dispatch.

Search branches are synthetic, no requests, accounts or ACSM downloads.
"""
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_gallica import GallicaSession


class Poison:
    def __str__(self):
        raise AssertionError("PRIVATE_ACCOUNT_TOKEN")


class PrivateString(str):
    pass


def session():
    obj = object.__new__(GallicaSession)
    obj.include_audio = True
    obj._diagnostic = lambda *_args, **_kwargs: None
    calls = []
    obj._search_ebooks = lambda *_a: calls.append("epub") or []
    obj._search_audio = lambda *_a: calls.append("audio") or []
    return obj, calls


@pytest.mark.parametrize("invalid", [
    Poison(), PrivateString("PRIVATE_USER"), {}, [], b"SECRET",
    1, 0, True, "x" * 2049, "x\ny", "x\x00y",
    "x\u202ePRIVATE",
])
def test_invalid_query_fails_without_str_or_branch(invalid):
    obj, calls = session()
    obj.last_search_warnings = ["previous identity"]
    with pytest.raises(ProviderCapabilityError, match="invalid_query") as caught:
        obj.search(invalid)
    assert obj.last_search_warnings == []
    assert calls == []
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("invalid_format", [
    None, "mp3", {"epub": 1}, ("epub", 1), ("epub", object()),
])
def test_invalid_content_types_are_rejected_even_on_short_query(invalid_format):
    obj, calls = session()
    with pytest.raises(ProviderCapabilityError, match="unsupported_content_type"):
        obj.search("x", content_types=invalid_format)
    assert calls == []
    assert obj.last_search_warnings == []


def test_existing_short_general_query_remains_noop_and_clears_warning():
    obj, calls = session()
    obj.last_search_warnings = ["prior"]
    assert obj.search(None, content_types=()) == []
    assert obj.last_search_warnings == []
    assert calls == []
