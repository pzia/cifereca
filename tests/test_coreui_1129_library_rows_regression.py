"""Library projection stays pure and preserves identity-relevant metadata (#1129)."""

from cifereca.gui.library_rows import project_library_rows


def _project(entries):
    return project_library_rows(
        entries,
        row_factory=lambda *args: args,
        provider_display_name=lambda provider: "Source " + provider,
        availability_label=lambda item: str(item.get("availability") or ""),
        backup_status_label=lambda status: "Sync " + status,
        format_date=lambda date: "FR:" + date,
    )


def test_empty_snapshot_has_no_rows_and_does_not_fetch():
    assert _project([]) == ([], {})


def test_multisource_label_and_due_date_keep_order_and_object_identity():
    entry = {"key": "identity:A:book", "title": "Livre", "providers": ["kobo", "local"],
             "availability": "Prêt", "due_at": "2026-12-01", "provider_id": "kobo",
             "entitlement_type": "loan"}
    rows, objects = _project([entry])
    assert rows[0][0] == "identity:A:book"
    assert rows[0][3] == "Prêt · retour FR:2026-12-01"
    assert rows[0][4] == "2 sources"
    assert objects["identity:A:book"] is entry


def test_sync_status_is_suppressed_for_local_copy():
    entries = [{"key": "a", "sync_status": "pending", "local": True},
               {"key": "b", "sync_status": "pending", "local": False}]
    rows, _ = _project(entries)
    assert rows[0][3] == ""
    assert rows[1][3] == "Sync pending"


def test_device_presence_and_pending_keep_have_distinct_labels():
    rows, _ = _project([
        {"device_statuses": [{"observed_state": "present"}, {"desired_state": "keep"}]},
        {"device_statuses": [{"observed_state": "missing", "desired_state": "keep"}]},
    ])
    assert rows[0][3] == "Kobo 1/2"
    assert rows[1][3] == "Kobo à synchroniser"


def test_duplicate_key_preserves_both_rows_and_last_lookup_mapping():
    a = {"key": "same", "title": "A"}
    b = {"key": "same", "title": "B"}
    rows, objects = _project([a, b])
    assert [row[1] for row in rows] == ["A", "B"]
    assert objects["same"] is b
