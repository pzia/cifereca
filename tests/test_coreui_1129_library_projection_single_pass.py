"""Library projection must consume filtered snapshots only once (#1129)."""

from cifereca.gui.library_rows import project_library_rows


def _project(items, **overrides):
    opts = dict(
        row_factory=lambda *args: args,
        provider_display_name=lambda provider: provider,
        availability_label=lambda item: "Disponible",
        backup_status_label=lambda status: status,
        format_date=lambda value: value,
    )
    opts.update(overrides)
    return project_library_rows(items, **opts)


def test_streaming_snapshot_is_consumed_once_in_original_order():
    visited = []
    def entries():
        for key in ("one", "two", "three"):
            visited.append(key)
            yield {"key": key, "title": key}
    rows, mapping = _project(entries())
    assert visited == ["one", "two", "three"]
    assert [row[0] for row in rows] == visited
    assert list(mapping) == visited


def test_projection_does_not_change_original_book_metadata():
    entry = {"key": "one", "providers": ["local", "kobo"],
             "device_statuses": [{"desired_state": "keep", "observed_state": "missing"}]}
    original = {"key": "one", "providers": ["local", "kobo"],
                "device_statuses": [{"desired_state": "keep", "observed_state": "missing"}]}
    _, mapping = _project([entry])
    assert entry == original
    assert mapping["one"] is entry


def test_projection_never_invokes_injected_formatters_without_corresponding_data():
    called = []
    _project([{"key": "one"}],
             format_date=lambda value: called.append("date"),
             backup_status_label=lambda value: called.append("backup"))
    assert called == []
