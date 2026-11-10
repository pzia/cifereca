"""#2385/#4552: raw descriptive source fact contract, no rights or network."""
from dataclasses import replace

import pytest

from cifereca.core.notice_source_observation import (
    NoticeSourceKey, admit_source_notice,
)


PUBLIC = NoticeSourceKey(
    provider_id="bnf", visibility="public", identity_id=None,
    credential_id=None, account_id=None,
    branch_id="sru", record_id="ark:one", edition_id="isbn:9780306406157",
    manifestation_role="print",
)
PRIVATE = replace(
    PUBLIC, provider_id="numilog", visibility="private",
    identity_id="alice", credential_id="card-alice", account_id="account-alice",
    branch_id="library", record_id="product:one",
    manifestation_role="digital",
)


def admit(key=PUBLIC, raw=None, **changed):
    return admit_source_notice(
        key=key, parser_id=changed.get("parser_id", "bnf-marc"),
        parser_version=changed.get("parser_version", "1.2.0"),
        observed_at=changed.get("observed_at", 1000),
        raw_fields={"Collection": "Série A", "public": {"min_age": 13}} if raw is None else raw,
        status=changed.get("status", "complete"),
    )


def test_raw_fields_keep_exact_names_and_shapes_without_core_mapping():
    original = {
        "Collection": "Saga A", "collection": "éditeur sans série",
        "333$a": "À partir de 13 ans",
        "attributes": {"Edition": "Papier", "Age": [8, 12, None]},
        "legacy": [False, 4.25, {"trace": "public"}],
    }
    obs = admit(raw=original)
    assert obs.fields_copy() == original
    assert "series" not in obs.fields_copy()
    assert "recommended_age" not in obs.fields_copy()
    assert obs.status == "complete"
    assert obs.source_revision.startswith("notice-raw.v1:")
    assert "Saga A" not in repr(obs)


def test_canonical_revision_is_order_invariant_and_no_input_alias():
    value = {"z": "end", "a": {"Y": 1, "X": 2}}
    a = admit(raw=value)
    b = admit(raw={"a": {"X": 2, "Y": 1}, "z": "end"}, observed_at=3000)
    assert a.source_revision == b.source_revision
    value["a"]["Y"] = 100
    assert a.fields_copy()["a"]["Y"] == 1
    copy = a.fields_copy()
    copy["a"]["Y"] = 9
    assert a.fields_copy()["a"]["Y"] == 1


def test_each_profile_and_credential_has_distinct_raw_revision():
    first = admit(key=PRIVATE)
    another_profile = admit(key=replace(
        PRIVATE, identity_id="bob", credential_id="card-bob", account_id="account-bob",
    ))
    another_account = admit(key=replace(
        PRIVATE, account_id="second-borrower",
    ))
    assert len({
        first.source_revision, another_profile.source_revision,
        another_account.source_revision,
    }) == 3
    assert first.key.identity_id == "alice"


def test_same_title_different_editions_no_implicit_merge_or_rights():
    a = admit(raw={"Title": "Même livre", "ebook": False})
    b = admit(key=replace(PUBLIC, edition_id="isbn:9781234567897"),
              raw={"Title": "Même livre", "ebook": True})
    assert a.source_revision != b.source_revision
    assert a.key.edition_id != b.key.edition_id
    assert not hasattr(a, "entitlement")
    assert not hasattr(a, "borrowable")


def test_partial_snapshot_is_descriptive_not_a_tombstone():
    old = admit(raw={"Title": "Livre", "Age": "13+"})
    partial = admit(raw={"Title": "Livre"}, status="partial")
    assert partial.status == "partial"
    assert old.source_revision != partial.source_revision
    assert not hasattr(partial, "delete")


@pytest.mark.parametrize("key", [
    replace(PUBLIC, visibility="private"),
    replace(PUBLIC, identity_id="alice"),
    replace(PRIVATE, credential_id=None),
    replace(PRIVATE, identity_id=None),
    replace(PRIVATE, account_id="bad key?"),
    replace(PRIVATE, visibility="public"),
    replace(PUBLIC, edition_id="bad edition?"),
    replace(PUBLIC, visibility="embedded", identity_id="alice",
            record_id="sha256:bad"),
])
def test_invalid_private_or_embedded_scope_fails_closed(key):
    with pytest.raises(ValueError, match="invalid_source_notice_observation"):
        admit(key=key)


def test_embedded_exact_sha_is_local_and_has_no_supplier_credential():
    embedded = replace(
        PUBLIC, provider_id="embedded-epub", visibility="embedded",
        identity_id="alice", record_id="sha256:" + "a" * 64,
    )
    v = admit(key=embedded, raw={"opf:meta": "Original title"})
    assert v.key.identity_id == "alice"
    assert v.fields_copy()["opf:meta"] == "Original title"
    with pytest.raises(ValueError, match="invalid_source_notice_observation"):
        admit(key=replace(embedded, credential_id="secret"))


@pytest.mark.parametrize("bad", [
    {"accessToken": "PRIVATE"}, {"refresh_token": "PRIVATE"},
    {"authorization": "Basic PRIVATE"}, {"signedUrl": "https://x/y"},
    {"Cookie": "PRIVATE"}, {"nested": [{"password": "no"}]},
    {"cover": "https://catalogue.example/image?x-amz-signature=private"},
    {"cover": "https://user:password@example.org/book"},
    {"cover": "https://example.org/book#frag"},
])
def test_source_evidence_never_persists_obvious_auth_material(bad):
    with pytest.raises(ValueError, match="invalid_source_notice_observation"):
        admit(raw=bad)


@pytest.mark.parametrize("bad", [
    {1: "not JSON object key"},
    {"value": object()},
    {"values": (1, 2)},
    {"bad_utf8": "\ud800"},
    {"nan": float("nan")},
    {"huge": "x" * 9000},
    {"depth": [[[[[[[[[1]]]]]]]]]},
    {"oversized": ["x"] * 4097},
    {"giant": {f"k{i}": 1 for i in range(257)}},
])
def test_non_json_excessive_or_unbounded_parser_output_fails_closed(bad):
    with pytest.raises(ValueError, match="invalid_source_notice_observation"):
        admit(raw=bad)


def test_raw_size_bounded_below_64k_even_when_individual_fields_are_small():
    oversized = {f"k{i}": "a" * 800 for i in range(180)}
    with pytest.raises(ValueError, match="invalid_source_notice_serialization"):
        admit(raw=oversized)


def test_revision_changes_on_parser_version_branch_visibility_and_status():
    original = admit()
    changed = [
        admit(parser_version="1.2.1"),
        admit(key=replace(PUBLIC, branch_id="other")),
        admit(key=replace(PUBLIC, manifestation_role="digital")),
        admit(status="partial"),
    ]
    assert all(other.source_revision != original.source_revision for other in changed)


@pytest.mark.parametrize("values", [
    {"observed_at": 0}, {"observed_at": True},
    {"parser_version": "bad version"}, {"status": "deleted"},
])
def test_invalid_timestamp_version_or_status_is_rejected(values):
    with pytest.raises(ValueError, match="invalid_source_notice_observation"):
        admit(**values)


@pytest.mark.parametrize("url", [
    "HTTPS://catalogue.example/epub?X-Amz-Signature=private",
    "  hTtPs://catalogue.example/epub?ACCESS_TOKEN=private",
    "//catalogue.example/epub?sig=private",
    " \t//catalogue.example/epub?refresh_token=private",
    "HTTP://user:password@catalogue.example/book",
    "//user:password@catalogue.example/book",
    " HTTPS://catalogue.example/book#private",
])
def test_source_url_security_is_scheme_insensitive_and_protocol_relative(url):
    with pytest.raises(ValueError, match="invalid_source_notice_observation"):
        admit(raw={"cover": url})


def test_harmless_uppercase_or_protocol_relative_url_remains_raw():
    raw = {"links": ["HTTPS://catalogue.example/book?lang=fr",
                     "//catalogue.example/cover.jpg"]}
    assert admit(raw=raw).fields_copy() == raw


@pytest.mark.parametrize("url", [
    "https://cdn.example/c.jpg?access-token=SECRET",
    "https://cdn.example/c.jpg?Access.Token=SECRET",
    "https://cdn.example/c.jpg?api-key=SECRET",
    "https://cdn.example/c.jpg?X-Amz-Security-Token=SECRET",
    "https://cdn.example/c.jpg?X-Amz-Algorithm=AWS4-HMAC-SHA256",
    "https://cdn.example/c.jpg?AWSAccessKeyId=SECRET",
    "https://cdn.example/c.jpg?%61ccess%2Dtoken=SECRET",
    "//cdn.example/c.jpg?authorization=SECRET",
])
def test_notice_source_refuses_obfuscated_query_credentials(url):
    with pytest.raises(ValueError, match="invalid_source_notice_observation"):
        admit(raw={"cover": url})


def test_notice_source_keeps_public_rendering_query_without_authorization():
    raw = {"cover": "https://cdn.example/c.jpg?size=medium&t=1717&lang=fr"}
    assert admit(raw=raw).fields_copy() == raw
