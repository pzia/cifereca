"""P0 #2797/#2714: human-only title/author fallback when no exact ISBN lead.

Mixed Kobo/Numilog/Paris, two identities, same-title different-author books,
duplicate files and partial inventory. The evidence graph and SQLite are
read-only; descriptive strings cannot become proof of a licence or ownership.
"""
from __future__ import annotations

from dataclasses import replace
import json
import sqlite3
from types import SimpleNamespace

from cifereca.core.library_audit import audit_library
from cifereca.core.library_descriptive_hints import (
    _fold, descriptive_file_indices, index_file_descriptions,
)
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_library_reconciliation import epub, filesystem, fixture_db


def _book(path, *, title="Le Secret des îles", author="Anaïs Dupré"):
    epub(path, xml=(
        "<package><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>"
        f"<dc:title>{title}</dc:title><dc:creator>{author}</dc:creator>"
        "</metadata></package>"
    ))


def _rig(tmp_path):
    root = tmp_path / "PRIVATE_ALICE"
    root.mkdir()
    foreign = tmp_path / "PRIVATE_FOREIGN"
    foreign.mkdir()
    first = root / "PRIVATE_First.epub"
    second = root / "PRIVATE_Second.epub"
    contradiction = root / "PRIVATE_Contradiction.epub"
    outsider = foreign / "PRIVATE_Other.epub"
    _book(first)
    _book(second, title="Le secret des iles", author="ANAÏS   DUPRÉ")
    _book(contradiction, author="Another Author")
    _book(outsider)
    database = tmp_path / "PRIVATE.sqlite"
    fixture_db(database)
    with sqlite3.connect(database) as db:
        for provider, kind in (("numilog", "owned"), ("kobo", "owned"), ("paris", "loan")):
            db.execute(
                "INSERT INTO entitlements (entitlement_id,identity_id,provider_id,"
                "credential_id,account_id,item_id,entitlement_type,state,"
                "metadata_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"PRIVATE-{provider}-right", "alice", provider,
                    f"PRIVATE-{provider}-credential", "PRIVATE-account",
                    f"PRIVATE-{provider}-edition", kind, "active",
                    json.dumps({
                        "title": "LE SECRET DES ÎLES",
                        "author": "Anais Dupre",
                        "source": (
                            "numilog.authenticated_library" if provider == "numilog"
                            else "kobo.personal_manifest" if provider == "kobo"
                            else "paris.loan_list"
                        ),
                    }),
                ),
            )
        db.execute(
            "INSERT INTO entitlements (entitlement_id,identity_id,provider_id,"
            "credential_id,account_id,item_id,entitlement_type,state,"
            "metadata_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "PRIVATE-other-right", "foreign", "numilog",
                "PRIVATE-other-cred", "PRIVATE-other-account",
                "PRIVATE-other-edition", "owned", "active",
                json.dumps({"title": "Le Secret des îles", "author": "Anaïs Dupré"}),
            ),
        )
    return root, foreign, database


def test_mixed_provider_title_author_leads_are_separate_from_exact_rights(tmp_path):
    root, foreign, db = _rig(tmp_path)
    before = filesystem(tmp_path)
    audit = audit_library(db, {"alice": (root,), "foreign": (foreign,)})
    session = LocalInspection(audit, lambda: True, "alice", roots=(root,))
    try:
        assert not audit.metadata_candidates.candidates
        for provider in ("numilog", "kobo", "paris"):
            rights = session.page(
                provider=provider, state="rights_without_verified_file",
            ).cases
            assert len(rights) == 1
            right = rights[0]
            # Legacy ISBN only candidates are untouched and still absent.
            assert session.candidate_files(right.key) == ()
            hints = session.descriptive_files(right.key)
            assert len(hints) == 2
            assert all(h.key.startswith("file:") for h in hints)
            assert all("Piste descriptive seulement" in h.details for h in hints)
            assert all("PRIVATE_Other" not in h.details for h in hints)
            assert all("PRIVATE_Contradiction" not in h.details for h in hints)
            assert "Pistes descriptives distinctes" in right.details
            assert "aucune attribution individuelle" in right.details
            for file in hints:
                check = session.preflight_manual_link(right.key, file.key)
                assert check.read_only and not check.attachment_authorized
                assert session.preview_manual_attachment(right.key, file.key) == {
                    "status": "unavailable", "read_only": True,
                }
        public = json.dumps(audit.redacted())
        for private in ("PRIVATE", "secret des", "anais", "dup"):  # no local metadata leaks
            assert private.lower() not in public.lower()
    finally:
        session.close()
    assert not session.valid()
    assert session._descriptive_file_keys == {}
    assert filesystem(tmp_path) == before


def test_partial_inventory_still_offers_observed_descriptive_leads_without_authority(tmp_path):
    root, _foreign, db = _rig(tmp_path)
    audit = audit_library(db, {"alice": (root,)})
    audit.inventory.reasons.add("time_budget")
    session = LocalInspection(audit, lambda: True, "alice", roots=(root,))
    try:
        right, = session.page(
            provider="numilog", state="rights_without_verified_file",
        ).cases
        hints = session.descriptive_files(right.key)
        assert len(hints) == 2
        for hint in hints:
            decision = session.preflight_manual_link(right.key, hint.key)
            assert decision.read_only and not decision.attachment_authorized
        assert not session.candidate_files(right.key)
    finally:
        session.close()


def test_descriptive_index_requires_both_exact_normalized_title_and_author():
    assert _fold("  L'ÉTÉ — des  Îles  ") == "l ete des iles"
    rows = [
        SimpleNamespace(identity_id="alice", sha256="a"*64, validation="ok",
                        media_format="epub", metadata={
                            "title": "L'été des îles", "authors": ["Anaïs Dupré"],
                        }),
        SimpleNamespace(identity_id="bob", sha256="b"*64, validation="ok",
                        media_format="epub", metadata={
                            "title": "L'été des îles", "authors": ["Anaïs Dupré"],
                        }),
        SimpleNamespace(identity_id="alice", sha256="c"*64, validation="ok",
                        media_format="epub", metadata={
                            "title": "L'été des îles", "authors": ["Autre Nom"],
                        }),
        SimpleNamespace(identity_id="alice", sha256=None, validation="ok",
                        media_format="epub", metadata={
                            "title": "L'été des îles", "authors": ["Anaïs Dupré"],
                        }),
    ]
    states = {i: {"local_file_without_attested_right"} for i in range(len(rows))}
    index = index_file_descriptions(rows, states, identity_id="alice")
    assert descriptive_file_indices(
        index, title="L'ETE des ILES", authors="Anais Dupre",
    ) == (0,)
    assert not descriptive_file_indices(
        index, title="L'ETE des ILES", authors="No match",
    )
    assert not descriptive_file_indices(
        index, title="Autre titre", authors="Anais Dupre",
    )
    states[0] = {"confirmed_link"}
    assert not index_file_descriptions(rows, states, identity_id="alice")


def test_title_author_guesses_are_bounded_and_do_not_guess_from_filename_or_isbn():
    rows = [
        SimpleNamespace(identity_id="alice", sha256="a"*64, validation="ok",
                        media_format="epub", metadata={
                            "title": "Un même titre", "authors": ["Même Auteur"],
                            "identifiers": [{"scheme": "ISBN", "value": "9780306406157"}],
                        })
        for _ in range(60)
    ]
    states = {i: {"local_file_without_attested_right"} for i in range(60)}
    index = index_file_descriptions(rows, states, identity_id="alice")
    assert len(index[("un meme titre", "meme auteur")]) == 50
    assert len(descriptive_file_indices(
        index, title="Un meme titre", authors="Meme Auteur",
    )) == 20
    assert descriptive_file_indices(
        index, title="Un meme titre", authors="Meme Auteur", exclude={0, 1},
        max_results=3,
    ) == (2, 3, 4)
    assert not descriptive_file_indices(index, title="", authors="Meme Auteur")
    assert not descriptive_file_indices(index, title="Untitled", authors="Meme Auteur")
