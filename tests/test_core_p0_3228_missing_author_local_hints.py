"""#3228: manual EPUBs with missing authors must remain selectable for review.

Two source labels, true local EPUB/SQLite inspection and exact right scopes.
A title-only hint is not a supplier/edition/loan/ADEPT proof or write permit.
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from cifereca.core.library_audit import audit_library
from cifereca.core.library_descriptive_hints import (
    index_file_titles, title_only_file_indices,
)
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_library_reconciliation import epub, filesystem, fixture_db


def _book(path, *, title="A Shared Story", author=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    creator = f"<dc:creator>{author}</dc:creator>" if author is not None else ""
    epub(path, xml=(
        "<package><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>"
        f"<dc:title>{title}</dc:title>{creator}"
        "</metadata></package>"
    ))


def _inspection(tmp_path, *, provider, right_author=None, file_author=None,
                foreign=False, second_author=None):
    root = tmp_path / "reader" / "manual"
    _book(root / "old-acsm" / "legacy.epub", author=file_author)
    if second_author is not None:
        _book(root / "another" / "same-title.epub", author=second_author)
    roots = {"reader": (root,)}
    if foreign:
        other_root = tmp_path / "other" / "manual"
        _book(other_root / "foreign.epub", author=file_author)
        roots["other"] = (other_root,)
    database = tmp_path / "library.sqlite"
    fixture_db(database)
    meta = {"title": "A Shared Story"}
    if right_author is not None:
        meta["author"] = right_author
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO entitlements "
            "(entitlement_id,identity_id,provider_id,credential_id,account_id,"
            "item_id,entitlement_type,state,loan_id,metadata_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "right", "reader", provider, "credential", "borrower",
                "edition-1", "loan", "active", "loan-42", json.dumps(meta),
            ),
        )
    before = filesystem(tmp_path)
    audit = audit_library(database, roots)
    inspection = LocalInspection(audit, lambda: True, "reader", roots=(root,))
    return inspection, audit, before


@pytest.mark.parametrize("provider", ["bnf-pret", "synthetic-reader"])
def test_missing_right_author_offers_private_title_only_manual_epub(
    tmp_path, provider,
):
    inspection, audit, before = _inspection(
        tmp_path, provider=provider, right_author=None,
        file_author="Alice", foreign=True,
    )
    try:
        rights = inspection.page(
            state="rights_without_verified_file", provider=(
                "bnf-pret" if provider == "bnf-pret" else "other"
            ),
        ).cases
        right, = rights
        assert inspection.candidate_files(right.key) == ()
        assert inspection.descriptive_files(right.key) == ()
        hints = inspection.title_only_files(right.key)
        assert len(hints) == 1
        assert "legacy.epub" in hints[0].details
        assert "Piste au TITRE SEUL" in hints[0].details
        assert "Aucun droit, compte, prêt, provenance ADEPT" in hints[0].details
        assert "TITRE SEUL" in right.details
        assert "foreign.epub" not in str(hints)
        assert inspection.preflight_manual_link(
            right.key, hints[0].key,
        ).attachment_authorized is False
        assert "A Shared Story" not in repr(audit.redacted())
        # Reading hints is not acquisition, licence mutation or file copy.
        assert filesystem(tmp_path) == before
    finally:
        inspection.close()


def test_missing_file_author_is_a_weak_lead_even_if_right_has_author(tmp_path):
    inspection, _audit, before = _inspection(
        tmp_path, provider="bnf-pret", right_author="Alice",
        file_author=None,
    )
    try:
        right, = inspection.page(
            provider="bnf-pret", state="rights_without_verified_file",
        ).cases
        assert inspection.descriptive_files(right.key) == ()
        hint, = inspection.title_only_files(right.key)
        assert "auteur manquant" in hint.details
        assert filesystem(tmp_path) == before
    finally:
        inspection.close()


def test_two_known_different_authors_are_not_title_only_candidates(tmp_path):
    inspection, _audit, before = _inspection(
        tmp_path, provider="bnf-pret", right_author="Alice",
        file_author="Bob",
    )
    try:
        right, = inspection.page(
            provider="bnf-pret", state="rights_without_verified_file",
        ).cases
        assert inspection.descriptive_files(right.key) == ()
        assert inspection.title_only_files(right.key) == ()
        assert filesystem(tmp_path) == before
    finally:
        inspection.close()


def test_missing_author_multiple_same_title_files_remain_ambiguous(tmp_path):
    inspection, _audit, before = _inspection(
        tmp_path, provider="bnf-pret", file_author="Alice",
        second_author="Bob",
    )
    try:
        right, = inspection.page(
            provider="bnf-pret", state="rights_without_verified_file",
        ).cases
        hints = inspection.title_only_files(right.key)
        assert len(hints) == 2
        assert len({hint.key for hint in hints}) == 2
        assert all("Piste au TITRE SEUL" in hint.details for hint in hints)
        assert "Plusieurs fichiers portent ce titre" in right.details
        assert filesystem(tmp_path) == before
    finally:
        inspection.close()


def test_title_only_index_is_bounded_scoped_and_does_not_override_known_authors():
    files = [
        SimpleNamespace(
            identity_id="reader", sha256="a" * 64,
            validation="ok", media_format="epub",
            metadata={"title": "A Shared Story", "authors": ["Alice"]},
        )
        for _ in range(60)
    ]
    files.append(SimpleNamespace(
        identity_id="other", sha256="a" * 64, validation="ok",
        media_format="epub",
        metadata={"title": "A Shared Story", "authors": []},
    ))
    states = {i: {"local_file_without_attested_right"} for i in range(len(files))}
    index = index_file_titles(files, states, identity_id="reader")
    assert len(index["a shared story"]) == 50
    assert title_only_file_indices(
        index, title="a SHARED story", authors=None,
    ) == tuple(range(20))
    assert title_only_file_indices(
        index, title="A Shared Story", authors="Alice",
    ) == ()  # title+known author is a different, stronger candidate class
    assert title_only_file_indices(
        index, title="A Shared Story", authors=None,
        exclude=(0, 1), max_results=3,
    ) == (2, 3, 4)
    assert title_only_file_indices(index, title="Untitled", authors=None) == ()
