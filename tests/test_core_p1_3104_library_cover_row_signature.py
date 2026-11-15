"""P1 #3104: Library same-key cover and provenance updates invalidate GTK rows."""
from __future__ import annotations

from pathlib import Path

from cifereca.gui.library_rows import project_library_rows


ROOT = Path(__file__).resolve().parents[1]


class Row:
    def __init__(self, key, title, subtitle, meta, badge, thumbnail_url):
        self.key = key
        self.title = title
        self.subtitle = subtitle
        self.meta = meta
        self.badge = badge
        self.thumbnail_url = thumbnail_url


def project(entry, *, factory=Row):
    rows, objects = project_library_rows(
        [entry], row_factory=factory,
        provider_display_name=lambda provider: "Source " + provider,
        availability_label=lambda row: str(row.get("state") or ""),
        backup_status_label=lambda value: "Sync " + value,
        format_date=lambda value: value,
    )
    return rows[0], objects


def book(**kwargs):
    row = {
        "key": "artifact:exact-artifact",
        "identity_id": "alice", "provider_id": "bnf-pret",
        "item_id": "edition-a", "title": "Un titre", "author": "A.",
        "state": "local", "thumbnail_url": None,
    }
    row.update(kwargs)
    return row


def test_same_source_key_new_cover_invalidates_only_gtk_presentation():
    original = book()
    copy = book(thumbnail_url="file:///home/alice/cover.png")
    before, mapped = project(original)
    after, fresh = project(copy)
    assert before.key == after.key
    assert before._public_row_content != after._public_row_content
    assert before._public_row_content[6] is None
    assert after._public_row_content[6] == copy["thumbnail_url"]
    assert mapped[before.key] is original and fresh[after.key] is copy
    assert original["thumbnail_url"] is None  # no mutation of original model


def test_same_presentation_preserves_fast_path_and_cross_profile_is_not_reused():
    source = book(thumbnail_url="https://public.example/cover.jpg")
    left, _ = project(source)
    right, _ = project(dict(source))
    assert left._public_row_content == right._public_row_content
    foreign, _ = project(book(identity_id="bob", thumbnail_url=source["thumbnail_url"]))
    assert left.key == foreign.key
    assert left._public_row_content != foreign._public_row_content
    global_owner, _ = project(book(
        _library_view_owner="Un autre profil",
        thumbnail_url=source["thumbnail_url"],
    ))
    assert left._public_row_content != global_owner._public_row_content


def test_tuple_row_factory_legacy_contract_remains_unchanged():
    row, mapping = project(book(thumbnail_url=None), factory=lambda *fields: fields)
    assert row == (
        "artifact:exact-artifact", "Un titre", "A.", "local",
        "Source bnf-pret", None,
    )
    assert mapping[row[0]]["item_id"] == "edition-a"


def test_projector_only_stamps_existing_rows_without_provider_io():
    source = (ROOT / "src/cifereca/gui/library_rows.py").read_text(encoding="utf-8")
    master = (ROOT / "src/cifereca/gui/master_detail.py").read_text(encoding="utf-8")
    assert 'row._public_row_content = (' in source
    assert '"library", key, title, author, meta, provider_label, cover_url' in source
    assert 'getattr(old, "_public_row_content", None)' in master
    assert "service.library_entries(" not in source
    assert "service.holdings(" not in source
