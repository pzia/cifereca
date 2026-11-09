"""P0 #5055: completed manual receipts never authorize a provider operation."""
import pytest

from cifereca.core.manual_receipt_scope_lookup import exact_manual_receipt


def row(*, owner="alice", ref="scope-a", path="/private/file.epub"):
    return {"identity_id": owner, "provider_id": "manual",
            "artifact_id": "sha256:abc",
            "metadata": {"bnf_auto_ref": ref, "path": path}}


def lookup(rows):
    return exact_manual_receipt(
        rows, identity_id="alice",
        metadata_ref="bnf_auto_ref", expected_ref="scope-a",
    )


def test_one_exact_receipt_is_only_passive_candidate():
    exact = row()
    status, selected, path = lookup([row(owner="bob"), row(ref="other"), exact])
    assert status == "matched" and selected is exact
    assert str(path) == "/private/file.epub"
    assert not hasattr(selected, "download")  # a row is not an authority


def test_unrelated_identity_and_edition_do_not_match():
    assert lookup([row(owner="bob")]) == ("absent", None, None)
    assert lookup([row(ref="different")]) == ("absent", None, None)
    assert lookup([]) == ("absent", None, None)


def test_duplicate_exact_receipts_are_ambiguous():
    assert lookup([row(), row(path="/private/other.epub")]) == (
        "ambiguous", None, None,
    )
    assert lookup([row(), row()]) == ("ambiguous", None, None)


@pytest.mark.parametrize("malformed", [
    [row(path="")],
    [row(path=None)],
    [row(path={"unhashable": True})],
    [dict(row(), metadata=["not-a-map"])],
    [object()],
    None,
    [row()] * 100_001,
])
def test_malformed_or_oversized_evidence_fails_closed(malformed):
    assert lookup(malformed) == ("ambiguous", None, None)


def test_unrelated_private_file_is_never_opened_or_selected(tmp_path):
    private = tmp_path / "another-profile-secret.epub"
    private.write_bytes(b"private")
    status, selected, path = lookup([
        row(owner="other", path=str(private)),
        row(ref="other", path=str(private)),
    ])
    assert status == "absent" and selected is None and path is None
    assert private.read_bytes() == b"private"


def test_unknown_provider_may_reuse_shape_without_gaining_a_right():
    generic = exact_manual_receipt(
        [{"identity_id": "alice", "metadata": {
            "fixture_loan_ref": "exact", "path": "/private/book.epub"}}],
        identity_id="alice", metadata_ref="fixture_loan_ref",
        expected_ref="exact",
    )
    assert generic[0] == "matched"
    assert generic[1].get("entitlement_id") is None
