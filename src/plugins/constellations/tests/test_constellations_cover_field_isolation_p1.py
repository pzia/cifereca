"""#1599: one malformed provider field does not hide other attested covers.

No network, Gtk, library or provider session is used by this detail projection.
"""
from __future__ import annotations

import pytest

from types import SimpleNamespace as NS

from cifereca.plugin_api import BookRef
from constellations_page import constellation_detail_presentation


SAFE = "https://covers.example.org/edition-1.jpg"


class FailingMap(dict):
    def __init__(self, broken, **items):
        super().__init__(items)
        self.broken = broken

    def get(self, name, default=None):
        if name == self.broken:
            SecretType = type("Bearer_PRIVATE_CREDENTIAL_EXCEPTION", (RuntimeError,), {})
            raise SecretType("/home/PRIVATE_READER/secret.epub")
        return super().get(name, default)


class PoisonString(str):
    def __str__(self):
        raise AssertionError("Do not stringify untrusted subclass")


def project(record):
    node = NS(
        book=BookRef("edition-1", "Public book", ("A",)),
        sources=("Public catalogue",),
        availability=("externe",),
        url="",
        raw=record,
    )
    return constellation_detail_presentation(node)


@pytest.mark.parametrize("row", (
    FailingMap("cover_url", thumbnail_url=SAFE),
    FailingMap("cover_url", metadata={"cover_url": SAFE}),
    FailingMap("thumbnail_url", metadata={"search_result": {"cover_url": SAFE}}),
    FailingMap("metadata", cover_url=SAFE),
    {"cover_url": "https://covers.example.org/download/private.epub",
     "metadata": FailingMap("cover_url", thumbnail_url=SAFE)},
    {"cover_url": "https://covers.example.org/book.jpg?token=PRIVATE",
     "metadata": FailingMap("search_result", cover_url=SAFE)},
    {"cover_url": PoisonString(SAFE), "thumbnail_url": SAFE},
))
def test_broken_field_does_not_mask_an_independent_public_thumbnail(row):
    presentation = project(row)
    assert presentation.thumbnail_url == SAFE
    assert presentation.actions == ()
    assert "PRIVATE" not in str(presentation)


@pytest.mark.parametrize("row", (
    FailingMap("cover_url"),
    FailingMap("thumbnail_url", cover_url=None),
    {"metadata": FailingMap("cover_url")},
    {"cover_url": "file:///home/PRIVATE_ACCOUNT/book.epub"},
    {"metadata": {"search_result": {"thumbnail_url":
        "https://covers.example.org/acquisition/PRIVATE_LICENSE"}}},
))
def test_missing_or_unverifiable_cover_remains_absent(row):
    presentation = project(row)
    assert presentation.thumbnail_url is None
    assert presentation.actions == ()
    assert "PRIVATE" not in str(presentation)


def test_direct_public_cover_keeps_precedence_over_nested_candidates():
    projection = project({
        "cover_url": SAFE,
        "metadata": {"cover_url": "https://covers.example.org/different.jpg"},
    })
    assert projection.thumbnail_url == SAFE
