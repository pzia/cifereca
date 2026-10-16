"""#1599: Jeunesse list and public detail share one safe author projection.

Synthetic cached YouthBook values, no provider, network, GTK or file access.
"""
from youth_catalog import YouthBook
from youth_page import book_ref, detail_presentation, list_entry


class Poison:
    def __str__(self):
        raise AssertionError("Bearer PRIVATE_TOKEN /home/PRIVATE_USER")


def book(authors):
    return YouthBook(
        source_id="public-source",
        record_id="public-edition",
        title="Un livre",
        authors=authors,
    )


def test_list_and_detail_author_values_are_identical_and_trimmed():
    source = book((" Alice   Martin ", "Bob   Durand", "Alice Martin"))
    entry = list_entry(source)
    detail = detail_presentation(source)
    authors = [
        field.value for section in detail.sections
        for field in section.fields if field.kind == "author"
    ]
    assert authors == ["Alice Martin", "Bob Durand", "Alice Martin"]
    assert entry.subtitle == detail.subtitle == ", ".join(authors)


def test_bad_cached_author_values_cannot_leak_or_break_list_and_detail():
    source = book((
        "Alice", Poison(), "Bearer PRIVATE_PASSWORD\u202e",
        "PRIVATE\nSESSION", "Z" * 181, " Bob ",
    ))
    entry = list_entry(source)
    detail = detail_presentation(source)
    assert entry.subtitle == detail.subtitle == "Alice, Bob"
    authors = [
        field.value for section in detail.sections
        for field in section.fields if field.kind == "author"
    ]
    assert authors == ["Alice", "Bob"]
    assert "PRIVATE" not in str((entry.subtitle, detail.subtitle, authors))


def test_author_list_is_bounded_without_fake_or_truncated_names():
    source = book(tuple(f"Author {index}" for index in range(200)))
    entry = list_entry(source)
    detail = detail_presentation(source)
    values = [
        field.value for section in detail.sections
        for field in section.fields if field.kind == "author"
    ]
    assert len(values) == 32
    assert values[0] == "Author 0"
    assert values[-1] == "Author 31"
    assert entry.subtitle == detail.subtitle == ", ".join(values)


def test_non_string_author_or_absent_author_never_becomes_a_public_label():
    for authors in ((42, False, {}, Poison()), ()):
        source = book(authors)
        assert list_entry(source).subtitle == ""
        detail = detail_presentation(source)
        assert detail.subtitle == ""
        assert not any(
            field.kind == "author" for sec in detail.sections for field in sec.fields
        )


def test_bookref_navigation_preserves_exactly_the_public_author_projection():
    source = book((" Alice  Martin ", Poison(), "PRIVATE\nSESSION", "Bob", "Alice Martin"))
    reference = book_ref(source)
    entry = list_entry(source)
    detail = detail_presentation(source)
    assert reference.authors == ("Alice Martin", "Bob", "Alice Martin")
    assert entry.subtitle == detail.subtitle == ", ".join(reference.authors)
    assert "PRIVATE" not in repr(reference.authors)


def test_bookref_rejects_mapping_keys_and_string_subclasses_as_authors():
    class Untrusted(str):
        pass

    for authors in ({"PRIVATE_ACCOUNT": "Alice"}, Untrusted("PRIVATE_TOKEN")):
        source = book(authors)
        reference = book_ref(source)
        assert reference.authors == ()
        assert list_entry(source).subtitle == ""
        assert detail_presentation(source).subtitle == ""


def test_bookref_authors_are_bounded_like_visible_detail():
    source = book(tuple(f"Author {i}" for i in range(100)))
    assert len(book_ref(source).authors) == 32
    assert book_ref(source).authors == tuple(f"Author {i}" for i in range(32))
