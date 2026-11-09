"""Provider-neutral source labels in the headless recent projection (#5056)."""
from cifereca.gui.recent_rows import project_recent_rows


def test_descriptor_and_unknown_provider():
    labels = {"kobo": "Personal books", "numilog": "Purchased editions",
              "library.example": "Library X"}
    rows, keyed = project_recent_rows(
        [{"provider_id": name, "placement_id": name} for name in (*labels, "local")],
        row_factory=lambda *args: args,
        provider_display_name=lambda name: labels[name],
    )
    assert [row[4] for row in rows] == [
        "Personal books", "Purchased editions", "Library X", "Local"]
    assert len(keyed) == 4


def test_invalid_labels_never_expose_private_provider_errors():
    for value in ("", " private ", "x" * 81, "\nsecret", None, 99):
        rows, _ = project_recent_rows(
            [{"provider_id": "library.example"}],
            row_factory=lambda *args: args,
            provider_display_name=lambda _: value,
        )
        assert rows[0][4] == "Source"

    def broken(_):
        raise ValueError("token=SECRET")

    rows, _ = project_recent_rows(
        [{"provider_id": "library.example"}],
        row_factory=lambda *args: args,
        provider_display_name=broken,
    )
    assert rows[0][4] == "Source"
