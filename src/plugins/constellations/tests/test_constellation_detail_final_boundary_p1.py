"""#1599: Constellations' final public DetailPanel model is a trust boundary.

No GTK, network access, catalogue queries or filesystem operations.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import BookRef
from constellations_page import constellation_detail_presentation


class Poison:
    def __str__(self):
        raise AssertionError("Bearer PRIVATE_TOKEN /home/PRIVATE_PERSON")

    def __bool__(self):
        raise AssertionError("PRIVATE_UNEXPECTED_BOOLEAN")


def node(*, title="Livre", authors=("Alice",), sources=("BnF",),
         availability=("externe",), url="https://books.example.org/notice/1"):
    return NS(
        book=BookRef("edition-1", title, authors),
        sources=sources,
        availability=availability,
        url=url,
    )


@pytest.mark.parametrize("unsafe", (
    "file:///home/PRIVATE_PERSON/book.epub",
    "https://books.example.org/download/book.epub",
    "https://books.example.org/notice/1?token=PRIVATE_SECRET",
    "http://localhost:8080/private",
    "javascript:PRIVATE_TOKEN",
    "https://books.example.org/notice/1#PRIVATE_FRAGMENT",
    Poison(),
))
def test_final_detail_rejects_unverified_link_even_when_supplied_on_card(unsafe):
    detail = constellation_detail_presentation(node(url=unsafe))
    assert detail.links == ()
    assert detail.actions == ()
    assert "PRIVATE" not in str(detail)


def test_final_detail_keeps_attested_public_notice_without_new_actions():
    detail = constellation_detail_presentation(node())
    assert len(detail.links) == 1
    assert detail.links[0].url == "https://books.example.org/notice/1"
    assert detail.actions == ()


def test_untrusted_author_provenance_badges_never_coerce_or_leak():
    detail = constellation_detail_presentation(node(
        title=Poison(),
        authors=("Alice", Poison(), "Bearer PRIVATE_TOKEN\u202e", "Bob", "Z" * 200),
        sources=("BnF", Poison(), "PRIVATE_SECRET\u200b", "Paris"),
        availability=("externe", Poison(), "PRIVATE\nTOKEN"),
        url="",
    ))
    assert detail.title == ""
    assert detail.subtitle == "Alice, Bob"
    assert detail.links == ()
    assert detail.badges == ("externe",)
    sections = {sec.id: sec for sec in detail.sections}
    assert [(f.value, f.kind) for f in sections["bibliography"].fields] == [
        ("Alice", "author"), ("Bob", "author"),
    ]
    assert [(f.value, f.kind) for f in sections["provenance"].fields] == [
        ("BnF", "copy"), ("Paris", "copy"),
    ]
    assert "PRIVATE" not in str(detail)


def test_extremely_long_source_list_is_bounded_not_exhausted():
    class Endless:
        def __iter__(self):
            for value in ("Source" for _ in range(10_000)):
                yield value
            raise AssertionError("unbounded source iterator")

    detail = constellation_detail_presentation(node(
        authors=(), sources=Endless(), availability=(),
    ))
    source_section = next(s for s in detail.sections if s.id == "provenance")
    assert len(source_section.fields) == 32
    assert all(field.value == "Source" for field in source_section.fields)
