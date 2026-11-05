"""P1 #2854: per-book USB dry-run labels are scoped, ephemeral, inert."""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.device_usb_dry_run import UsbDryRun
from cifereca.core.device_usb_preview import UsbPreviewRow
from cifereca.gui.reader_library_projection import (
    ReaderLibraryBook, ReaderLibrarySnapshot,
)
from cifereca.gui.reader_usb_decisions import project_reader_usb_decisions


NOW = 1800001000
ID = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64


def book(*, artifact=ID, key="book-a", desired="keep",
         known="unknown", at=None, title="Livre local"):
    return ReaderLibraryBook(
        key=key, artifact_id=artifact, placement_id="placement-a",
        title=title, author="Autrice", thumbnail_url=None,
        desired_state=desired, current_presence="unknown",
        last_known=known, last_observed_at=at,
        label="À mettre · présence à vérifier",
    )


def library(*, identity="alice", device="usb-alice", books=None,
            present=False):
    return ReaderLibrarySnapshot(
        identity_id=identity, device_id=device,
        device_enabled=True,
        books=(book(),) if books is None else books,
        total_local=1, wanted_keep=1, wanted_remove=0, no_intention=0,
        current_presence_confirmed=present,
    )


def decision(*, artifact=ID, action="upload_candidate",
             desired="keep", observed="missing", known="unknown",
             at=None, reason="revalidate_before_usb_write"):
    return UsbPreviewRow(
        artifact_id=artifact, desired=desired, observed=observed,
        last_known=known, last_observed_at=at,
        action=action, reason=reason, executable=False,
    )


def report(*, identity="alice", device="usb-alice",
           timestamp=NOW, rows=None, status="scanned", attested=True):
    return UsbDryRun(
        identity_id=identity, device_id=device,
        status=status, scan_attested=attested, observed_at=timestamp,
        rows=(decision(),) if rows is None else rows,
    )


def project(r=None, l=None, *, now=NOW):
    return project_reader_usb_decisions(
        report=report() if r is None else r,
        library=library() if l is None else l, now=now,
    )


def test_missing_target_with_owned_sha_is_advisory_not_permission():
    outcome = project()
    assert outcome.status == "scanned"
    assert outcome.executable is False
    assert outcome.observed_at == NOW
    assert len(outcome.books) == 1
    row, = outcome.books
    assert row.book_key == "book-a"
    assert row.title == "Livre local"
    assert row.state_label == "Envoi à examiner · aucune copie effectuée"
    assert row.current_label == "Absence observée à la destination prévue"
    assert row.executable is False
    assert "nouvelle vérification" in outcome.message
    assert ID not in repr(outcome)
    assert "/home/" not in repr(outcome)


@pytest.mark.parametrize("action,reason,expected", [
    ("verify", "reader_formats_unconfigured", "non déclarés"),
    ("blocked", "reader_format_unsupported", "non déclaré compatible"),
    ("blocked", "protection_reported", "Protection DRM"),
    ("upload_candidate", "drm_and_transfer_rights_unverified",
     "droits DRM et de copie non vérifiés"),
])
def test_reader_format_decisions_are_explicit_and_never_copy(
    action, reason, expected,
):
    result = project(r=report(rows=(decision(action=action, reason=reason),)))
    assert result.status == "scanned"
    assert expected in result.books[0].reason_label
    assert result.books[0].executable is False
    assert "Aucun" in result.books[0].state_label or "aucune" in (
        result.books[0].state_label
    )


@pytest.mark.parametrize("action,reason,visible", [
    ("blocked", "content_structure_unverified", "Structure du fichier"),
    ("blocked", "content_archive_unbounded", "après décompression"),
    ("blocked", "content_protection_detected", "Protection détectée"),
    ("verify", "content_inspection_size_limit", "trop volumineux"),
    ("verify", "content_inspection_budget_exhausted", "Budget de revue"),
    ("verify", "content_bytes_unverified", "Octets locaux changés"),
    ("upload_candidate", "epub_drm_and_transfer_rights_unverified", "Structure EPUB valide"),
    ("upload_candidate", "pdf_drm_and_transfer_rights_unverified", "Structure PDF valide"),
])
def test_content_inspection_labels_are_closed_and_never_authorize(
    action, reason, visible,
):
    result = project(r=report(rows=(decision(action=action, reason=reason),)))
    assert result.status == "scanned"
    assert visible in result.books[0].reason_label
    assert result.books[0].executable is False
    assert result.executable is False


@pytest.mark.parametrize("action,reason,visible", [
    ("blocked", "target_occupied", "occupe"),
    ("blocked", "target_volume_read_only", "lecture seule"),
    ("blocked", "target_space_insufficient", "Espace libre insuffisant"),
    ("blocked", "target_batch_capacity_insufficient", "tous les envois"),
    ("verify", "target_mount_unverified", "Volume USB réel non attesté"),
    ("verify", "target_mount_changed", "Volume USB changé"),
    ("verify", "target_preflight_budget_exhausted", "par lots"),
])
def test_physical_target_preflight_reasons_never_authorize(
    action, reason, visible,
):
    result = project(r=report(rows=(decision(action=action, reason=reason),)))
    assert result.status == "scanned"
    assert visible in result.books[0].reason_label
    assert result.books[0].executable is False
    assert result.executable is False


def test_exact_matching_local_sha_is_skip_and_historic_observation_stays_historic():
    l = library(books=(book(known="present", at=1500000000),))
    r = report(rows=(decision(
        action="skip", observed="present", known="present", at=1500000000,
        reason="identical_sha_observed",
    ),))
    outcome = project(r, l)
    assert outcome.status == "scanned"
    assert outcome.books[0].state_label == "Aucune opération nécessaire"
    assert outcome.books[0].current_label == "Présence observée lors de cet examen"
    assert outcome.executable is False


def test_remove_without_receipt_shows_blocked_and_no_remove_button():
    l = library(books=(book(desired="remove"),))
    r = report(rows=(decision(
        action="blocked", desired="remove", observed="present",
        reason="unmanaged_or_changed_file",
    ),))
    outcome = project(r, l)
    assert outcome.status == "scanned"
    assert outcome.books[0].reason_label == (
        "Fichier non géré ou modifié ; retrait interdit"
    )
    assert outcome.books[0].executable is False
    assert "supprim" not in outcome.books[0].state_label.lower()


@pytest.mark.parametrize("bad", [
    report(identity="bob"),
    report(device="usb-bob"),
    report(status="device_unverified", attested=False, timestamp=None, rows=()),
    report(timestamp=NOW-301),
    report(timestamp=NOW+1),
    replace(report(), executable=True),
    report(attested=False),
    report(rows=(replace(decision(), executable=True),)),
    report(rows=(replace(decision(), desired="remove"),)),
    report(rows=(replace(decision(), observed="present"),)),
    report(rows=(replace(decision(), action="remove_candidate"),)),
    report(rows=(decision(), decision())),
    report(rows=(replace(decision(), action="unknown"),)),
])
def test_foreign_stale_or_crafted_report_is_never_displayed(bad):
    outcome = project(bad)
    assert outcome.status == "unverified"
    assert outcome.books == ()
    assert outcome.observed_at is None
    assert outcome.executable is False
    assert "périmé" in outcome.message


def test_reader_change_or_identity_change_rejects_entire_report():
    assert project(l=library(identity="bob")).status == "unverified"
    assert project(l=library(device="usb-bob")).status == "unverified"


def test_stale_library_desired_state_cannot_inherit_current_usb_observation():
    old = library(books=(book(desired="remove"),))
    assert project(l=old).status == "unverified"
    l = library(books=(book(known="present", at=1500000000),))
    assert project(l=l).status == "unverified"


def test_current_presence_must_not_be_preconfirmed_by_sqlite_projection():
    assert project(l=library(present=True)).status == "unverified"


def test_an_unrepresented_artifact_never_leaks_its_sha_or_title():
    r = report(rows=(
        decision(artifact=OTHER, action="blocked", observed="present",
                 reason="unmanaged_or_changed_file"),
        decision(),
    ))
    outcome = project(r)
    assert outcome.status == "scanned"
    assert len(outcome.books) == 1
    assert OTHER not in repr(outcome)
    assert "SECRET BOB" not in repr(outcome)


def test_filtered_library_subset_is_valid_without_disclosing_hidden_book():
    l = library(books=(book(artifact=OTHER, key="selected", title="Visible"),))
    r = report(rows=(decision(), decision(artifact=OTHER)))
    outcome = project(r, l)
    assert outcome.status == "scanned"
    assert len(outcome.books) == 1
    assert outcome.books[0].book_key == "selected"
    assert ID not in repr(outcome)


def test_no_intention_book_never_gets_a_fake_usb_decision():
    l = library(books=(book(desired="none"),))
    r = report(rows=())
    outcome = project(r, l)
    assert outcome.status == "scanned"
    assert outcome.books == ()
    assert outcome.executable is False


def test_no_intention_book_must_not_receive_a_removed_foreign_intent():
    l = library(books=(book(desired="none"),))
    assert project(l=l).status == "unverified"


def test_duplicate_library_entry_is_not_silently_deduplicated():
    l = library(books=(book(), book(key="duplicate")))
    assert project(l=l).status == "unverified"
    l = library(books=(book(), book(artifact=OTHER)))
    assert project(l=l).status == "unverified"


def test_untrusted_reason_never_printed_to_gui():
    raw = "PRIVATE_TOKEN /home/secret/signed?token=abc"
    r = report(rows=(replace(decision(), reason=raw),))
    outcome = project(r)
    assert outcome.status == "scanned"
    assert raw not in repr(outcome)
    assert outcome.books[0].reason_label == "Vérification complémentaire nécessaire"


def test_now_must_be_integer_and_fresh():
    assert project(now=NOW+300).status == "scanned"
    assert project(now=NOW+301).status == "unverified"
    assert project(now=True).status == "unverified"
    assert project(now=0).status == "unverified"


def test_projection_never_changes_inputs_or_calls_storage():
    l = library()
    r = report()
    result = project(r,l)
    assert l.books[0].current_presence == "unknown"
    assert r.rows[0].action == "upload_candidate"
    assert result.books[0].executable is False
    assert all(not hasattr(x, "execute") for x in result.books)
