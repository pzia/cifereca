"""#2835: SHA-bound OPF evidence -> actual reader projection, all 9 pairs."""
from dataclasses import replace

import pytest

from cifereca.core.device_usb_content_review import review_usb_content
from cifereca.core.device_usb_variant_review import _SUFFIX
from tests.test_p1_2854_usb_content_review import _declared_epub, case
from tests.test_p1_2854_usb_decision_details import book, decision, library, project, report


@pytest.mark.parametrize("fmt,motor", [
    ("epub", "moteur ADE"),
    ("kepub", "moteur Kobo WebKit"),
    ("fxl.kepub", "moteur Kobo WebKit pour le fixe"),
])
@pytest.mark.parametrize("declaration,layout,label", [
    ("pre-paginated", "fixed", "Mise en page fixe déclarée"),
    ("reflowable", "reflowable", "Mise en page adaptable déclarée"),
    (None, "unknown", "Mise en page non déterminée"),
])
def test_observed_layout_and_existing_suffix_are_independent(fmt, motor, declaration, layout, label):
    values = case(fmt, data=_declared_epub(declaration))
    structural = review_usb_content(**values)
    assert structural.status == "review" and structural.declared_layout == layout
    aid = values["artifact"]["artifact_id"]
    target = "Books/" + values["artifact"]["sha256"] + _SUFFIX[fmt]
    item = replace(book(artifact=aid), target_relative_path=target, target_format=fmt)
    row = replace(decision(artifact=aid, reason=structural.reason), declared_layout=layout)
    result = project(report(rows=(row,)), library(books=(item,)))
    assert result.status == "scanned" and not result.executable
    output, = result.books
    assert not output.executable and "aucune copie effectuée" in output.state_label
    assert label in output.reason_label and motor in output.reason_label
    assert "Sur Kobo eInk" in output.reason_label
    assert "Rendu et compatibilité non vérifiés" in output.reason_label
    assert ("notes et marque-pages" in output.reason_label) == (fmt != "epub")
    assert ("Spécificités KEPUB non attestées" in output.reason_label) == (fmt != "epub")
    mismatch = (fmt == "fxl.kepub" and layout != "fixed" or fmt == "kepub" and layout == "fixed")
    assert ("aucun renommage automatique" in output.reason_label) == mismatch
    assert target not in repr(result) and aid not in repr(result)


@pytest.mark.parametrize("fmt,layout,reason,action", [
    ("epub", "PRIVATE_LAYOUT", "epub_drm_and_transfer_rights_unverified", "upload_candidate"),
    ("epub", None, "epub_drm_and_transfer_rights_unverified", "upload_candidate"),
    ("fxl.kepub", "fixed", "epub_drm_and_transfer_rights_unverified", "upload_candidate"),
    ("pdf", "fixed", "pdf_drm_and_transfer_rights_unverified", "upload_candidate"),
    ("kepub", "fixed", "content_protection_detected", "blocked"),
    ("kepub", "fixed", "kepub_semantics_drm_and_transfer_rights_unverified", "verify"),
])
def test_no_engine_label_from_unknown_unobserved_or_mismatching_evidence(fmt, layout, reason, action):
    item = replace(book(), target_format=fmt)
    row = replace(decision(action=action, reason=reason), declared_layout=layout)
    result = project(report(rows=(row,)), library(books=(item,)))
    assert result.status == "scanned"
    assert "moteur " not in result.books[0].reason_label
    assert "PRIVATE_LAYOUT" not in repr(result)
    assert not result.executable
