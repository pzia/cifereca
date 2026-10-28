"""#3252: a Kobo HTTPS URL with contradictory DRM flags is not an offer.

Synthetic Vault descriptor only: no acquisition GET, ADEPT, Core mutation,
signed-token export or entitlement inference. Uses existing attested 1.4
DRM shapes (none/SignedNoDrm, adobe/AdobeDrm).
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca_kobo_delivery_offer import exact_kobo_offer_id
from tests.test_p1_3252_kobo_descriptor_response_scope import descriptor, SCOPE
from tests.test_p1_3252_kobo_selected_delivery_offer import setup


def qualify(candidate):
    payload = descriptor()
    payload["acquisition"]["candidates"] = [candidate]
    return exact_kobo_offer_id(payload, **SCOPE)


def plain():
    row = deepcopy(descriptor()["acquisition"]["candidates"][0])
    row["drm"] = {
        "raw_type": "SignedNoDrm", "family": "none", "protected": False,
    }
    return row


def adobe():
    row = plain()
    row["kind"] = "acsm"
    row["handling"] = "adobe_adept"
    row["media_format"] = "acsm"
    row["url"] = "https://acs4.kobo.com/fictional/book.acsm?token=PRIVATE"
    row["drm"] = {
        "raw_type": "AdobeDrm", "family": "adobe", "protected": True,
    }
    return row


def test_known_consistent_drm_shapes_are_only_opaque_candidates():
    assert qualify(plain()).startswith("kobo.descriptor:")
    assert qualify(adobe()).startswith("kobo.descriptor:")
    row = adobe()
    row["drm"]["family"] = "adobe_adept"
    assert qualify(row).startswith("kobo.descriptor:")


@pytest.mark.parametrize(("path", "bad"), [
    (("drm", "family"), "adobe"),
    (("drm", "family"), "kobo"),
    (("drm", "family"), ""),
    (("drm", "family"), True),
    (("drm", "protected"), True),
    (("drm", "protected"), None),
    (("drm", "protected"), "false"),
    (("root", "drm_family"), "adobe"),
    (("root", "drm_type"), "AdobeDrm"),
    (("root", "protected"), True),
    (("root", "protected"), 0),
])
def test_signednodrm_cannot_hide_conflicting_family_or_protected(path, bad):
    row = plain()
    if path[0] == "root":
        row[path[1]] = bad
    else:
        row["drm"][path[1]] = bad
    assert qualify(row) == ""


@pytest.mark.parametrize(("path", "bad"), [
    (("drm", "family"), "none"),
    (("drm", "family"), "kobo"),
    (("drm", "family"), None),
    (("drm", "protected"), False),
    (("drm", "protected"), 1),
    (("root", "drm_family"), "none"),
    (("root", "drm_type"), "SignedNoDrm"),
    (("root", "protected"), False),
])
def test_adobe_acsm_cannot_hide_conflicting_family_or_protected(path, bad):
    row = adobe()
    if path[0] == "root":
        row[path[1]] = bad
    else:
        row["drm"][path[1]] = bad
    assert qualify(row) == ""


@pytest.mark.parametrize("bad", [
    {"raw_type": "SignedNoDrm", "family": "adobe", "protected": False},
    {"raw_type": "SignedNoDrm", "family": "none", "protected": True},
    {"raw_type": "AdobeDrm", "family": "none", "protected": True},
    {"raw_type": "AdobeDrm", "family": "adobe", "protected": False},
])
def test_session_preserves_owned_right_but_refuses_untrusted_drm_offer(bad):
    session, vault, broker, account, calls = setup()
    def mismatched(product_id, **opts):
        calls.append("POST")
        payload = descriptor()
        payload["acquisition"]["candidates"][0]["drm"] = bad
        return payload
    vault.acquisition = mismatched
    try:
        report = session.rights_delivery_offer_snapshot(
            account, "book-1", explicit_user_action=True,
        )
        assert calls == ["POST"] and vault.calls == 1
        assert len(report.rights) == 1
        assert report.rights[0].state == "active"
        assert report.rights[0].delivery == "unknown"
        assert report.rights[0].delivery_offer_id == ""
        assert not report.authorizes_mutation
    finally:
        session.close()
