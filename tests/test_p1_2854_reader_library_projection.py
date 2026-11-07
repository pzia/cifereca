"""P1 #2854: read-only reader Library projection, no cross-profile bleed."""
from copy import deepcopy
import pytest

from cifereca.gui.reader_library_projection import project_reader_library


def binder(*, identity="alice", device="reader-a", enabled=True,
           uuid="uuid-alice-0001"):
    return {"identity_id": identity, "device_id": device, "enabled": enabled,
            "metadata": {"usb_fingerprint": uuid}}


def local(*, owner="alice", artifact="same-sha", placement="place-a",
          state="present"):
    return {"identity_id": owner, "artifact_id": artifact,
            "placement_id": placement, "state": state,
            "path": "/confidential/not-for-display.epub"}


def device_row(*, owner="alice", device="reader-a", artifact="same-sha",
               desired="keep", observed="present", when=1600000000):
    return {"device_id": device, "artifact_id": artifact,
            "desired_state": desired, "observed_state": observed,
            "last_observed_at": when,
            "target_relative_path": "Books/Cifereca/file.epub",
            "metadata": {"intent_identity_id": owner}}


def entry(*, owner="alice", artifact="same-sha",
          title="Livre commun", key="alice-one"):
    return {"key": key, "identity_id": owner,
            "artifact_id": artifact, "title": title,
            "author": "Autrice", "local": True,
            "thumbnail_url": None}


def view(*, owner="alice", bound=None, locals=None, placements=None,
         entries=None, filter="all"):
    return project_reader_library(
        identity_id=owner, binding=bound if bound is not None else binder(),
        local_placements=locals if locals is not None else [local()],
        device_placements=placements if placements is not None else [device_row()],
        library_entries=entries if entries is not None else [entry()],
        view_filter=filter,
    )


def test_old_sqlite_present_never_claims_current_device_presence():
    snapshot = view()
    assert snapshot.device_id == "reader-a"
    assert snapshot.identity_id == "alice"
    assert snapshot.device_enabled is True
    assert snapshot.current_presence_confirmed is False
    assert snapshot.total_local == 1
    assert snapshot.wanted_keep == 1
    row, = snapshot.books
    assert row.desired_state == "keep"
    assert row.current_presence == "unknown"
    assert row.last_known == "present"
    assert row.last_observed_at == 1600000000
    assert row.label == "À mettre · présence à vérifier"
    assert "confidential" not in repr(snapshot)


def test_two_identities_one_sha_never_leak_titles_or_paths():
    rows = [local(owner="alice"), local(owner="bob", placement="place-b")]
    books = [
        entry(owner="bob", title="SECRET ALBUM B", key="bob-private"),
        entry(owner="alice", title="Livre Alice", key="alice-view"),
    ]
    snapshot = view(locals=rows, entries=books)
    assert len(snapshot.books) == 1
    assert snapshot.books[0].title == "Livre Alice"
    assert snapshot.books[0].placement_id == "place-a"
    assert "SECRET" not in repr(snapshot)


def test_no_cross_profile_ownership_from_identical_artifact_sha():
    snapshot = view(locals=[local(owner="bob", placement="shared-b")])
    assert snapshot.total_local == 0
    assert snapshot.books == ()


def test_three_devices_two_profiles_keep_separate_intentions():
    shared_books = [entry(owner="alice", title="Alice"),
                    entry(owner="bob", title="Bob", key="bob-key")]
    locals = [local(owner="alice"),
              local(owner="bob", placement="bob-own")]
    readers = (
        ("alice", "reader-a", "keep"),
        ("alice", "reader-b", "remove"),
        ("bob", "reader-c", "keep"),
    )
    for owner, device_id, expected in readers:
        result = view(
            owner=owner,
            bound=binder(identity=owner, device=device_id),
            locals=locals, entries=shared_books,
            placements=[device_row(owner=owner, device=device_id, desired=expected)],
        )
        assert len(result.books) == 1
        assert result.books[0].desired_state == expected
        assert result.books[0].current_presence == "unknown"
        assert result.books[0].title == ("Bob" if owner == "bob" else "Alice")


@pytest.mark.parametrize("filter,expected", [
    ("all", 2), ("to_send", 1), ("to_remove", 0),
    ("without_intent", 1),
])
def test_filters_count_only_eligible_books_for_one_profile(filter, expected):
    rows = [local(), local(artifact="other", placement="p2")]
    entries = [entry(), entry(artifact="other", key="two")]
    data = view(locals=rows, entries=entries, filter=filter)
    assert len(data.books) == expected
    assert data.total_local == expected
    assert data.wanted_keep + data.wanted_remove + data.no_intention == expected


def test_no_hidden_storage_mutation_or_external_call():
    binding, locals, devices, entries = binder(), [local()], [device_row()], [entry()]
    original = deepcopy((binding, locals, devices, entries))
    snapshot = view(bound=binding, locals=locals, placements=devices, entries=entries)
    assert snapshot.books[0].current_presence == "unknown"
    assert (binding, locals, devices, entries) == original


@pytest.mark.parametrize("broken", [
    {}, {"intent_identity_id": "bob"},
    {"intent_identity_id": None},
])
def test_foreign_or_legacy_intention_is_rejected_before_display(broken):
    row = device_row()
    row["metadata"] = broken
    with pytest.raises(ValueError, match="foreign or ambiguous"):
        view(placements=[row])


def test_reassigned_device_cannot_inherit_old_intentions():
    with pytest.raises(ValueError, match="foreign or ambiguous"):
        view(owner="bob", bound=binder(identity="bob"),
             placements=[device_row(owner="alice")],
             locals=[local(owner="bob")],
             entries=[entry(owner="bob", key="bob")])


def test_duplicate_device_intents_are_rejected_instead_of_deduplicated():
    with pytest.raises(ValueError, match="foreign or ambiguous"):
        view(placements=[device_row(), device_row()])


def test_missing_or_malformed_binding_is_not_a_usb_reader():
    for broken in (
        binder(identity="bob"),
        {**binder(), "device_id": ""},
        {**binder(), "metadata": {}},
        {**binder(), "metadata": {"usb_fingerprint": None}},
    ):
        with pytest.raises(ValueError):
            view(bound=broken)


def test_malformed_filter_or_unbounded_inputs_are_inert():
    with pytest.raises(ValueError, match="invalid reader"):
        view(filter="physical_present")
    with pytest.raises(ValueError, match="unbounded"):
        view(entries=[entry()] * 30001)


def test_remote_or_catalogue_rows_are_never_added_by_sha_alone():
    items = [entry(owner="alice", title="Local"),
             {**entry(owner="alice", title="Remote duplicate", key="remote"),
              "artifact_id": "remote-only"}]
    result = view(entries=items)
    assert len(result.books) == 1 and result.books[0].title == "Local"


def test_multiple_source_rows_with_shared_sha_produce_one_device_wish():
    items = [entry(title="Local"),
             entry(key="second-provider", title="Duplicated catalogue origin")]
    result = view(entries=items)
    assert len(result.books) == 1
    assert result.books[0].title == "Local"


def test_missing_unverified_or_tombstoned_local_file_is_not_a_reader_book():
    for state in ("missing", "dedup_cleanup_pending", "returned", None):
        result = view(locals=[local(state=state)])
        assert result.books == ()


@pytest.mark.parametrize("path", ["Other/outside.epub", "Books/Cifereca/../outside.epub"])
def test_unscoped_target_is_not_shown_in_book_detail(path):
    target = dict(device_row(), target_relative_path=path)
    book, = view(
        bound=dict(binder(), target_directory="Books/Cifereca"),
        placements=[target],
    ).books
    assert book.target_relative_path is None
    assert book.target_format is None
