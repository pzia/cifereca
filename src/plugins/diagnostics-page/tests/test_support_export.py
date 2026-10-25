from types import SimpleNamespace

import pytest

from diagnostics_support import (
    SupportReportExport, support_report_bytes, diagnostic_json_bytes,
)


class Dialog:
    def __init__(self):
        self.file = None
        self.destroyed = False

    def set_current_name(self, name):
        self.name = name

    def connect(self, signal, callback):
        self.callback = callback

    def show(self):
        pass

    def destroy(self):
        self.destroyed = True

    def get_file(self):
        return self.file


class Destination:
    def __init__(self, path="/tmp/support.txt", fail=False):
        self.path, self.fail, self.writes = path, fail, []

    def get_path(self):
        return self.path

    def replace_contents_async(self, *args):
        self.writes.append(args)
        self.callback = args[-2]

    def replace_contents_finish(self, result):
        if self.fail:
            raise OSError("token=PRIVATE_WRITE_ERROR")
        return True, "etag"


def exporter():
    dialog, messages, sensitive = Dialog(), [], []
    gtk = SimpleNamespace(
        FileChooserNative=SimpleNamespace(new=lambda *a: dialog),
        FileChooserAction=SimpleNamespace(SAVE=1),
        ResponseType=SimpleNamespace(ACCEPT=1),
    )
    gio = SimpleNamespace(FileCreateFlags=SimpleNamespace(PRIVATE=1),
                          Cancellable=lambda: SimpleNamespace(cancel=lambda: None))
    export = SupportReportExport(gtk, gio,
        SimpleNamespace(set_sensitive=sensitive.append), lambda: None,
        lambda: 'RAPPORT\ntoken=PRIVATE_TOKEN\nhttps://example.com/LOAN/private', messages.append)
    return export, dialog, messages, sensitive


def test_open_and_cancel_never_write_or_query_the_broker():
    export, dialog, messages, sensitive = exporter()
    export.choose()
    assert export.data is not None
    assert "PRIVATE_TOKEN" in export.data.decode()
    assert "LOAN/private" in export.data.decode()
    export.choose()  # Repeated button activation does not open a second chooser.
    export.response(dialog, 0)
    assert dialog.destroyed and export.data is None
    assert sensitive == [False, True]
    assert messages == []


@pytest.mark.parametrize("fail", [False, True])
def test_local_export_is_async_private_and_reports_only_safe_status(fail):
    export, dialog, messages, sensitive = exporter()
    export.choose()
    destination = Destination(fail=fail)
    dialog.file = destination
    export.response(dialog, 1)
    assert len(destination.writes) == 1
    data, etag, backup, flags, cancellable, callback, user_data = destination.writes[0]
    assert b"PRIVATE_TOKEN" in data and flags == 1 and backup is False
    assert messages == []  # Success is not announced before completion.
    callback(destination, None, user_data)
    assert "PRIVATE_WRITE_ERROR" not in repr(messages)
    assert ("enregistré" in messages[0]) is (not fail)
    assert export.data is None and sensitive[-1] is True


def test_remote_destination_refused_before_io():
    export, dialog, messages, _ = exporter()
    export.choose()
    destination = Destination(path=None)
    dialog.file = destination
    export.response(dialog, 1)
    assert destination.writes == []
    assert "fichier local" in messages[0]


def test_deactivation_suppresses_late_success_and_further_export():
    export, dialog, messages, _ = exporter()
    export.choose()
    destination = Destination()
    dialog.file = destination
    export.response(dialog, 1)
    export.close()
    destination.callback(destination, None, None)
    export.choose()
    assert messages == [] and export.dialog is None


@pytest.mark.parametrize("text", [None, object(), "x" * 1_000_001, "é" * 500_001, "bad\ud800"])
def test_oversized_invalid_or_unknown_report_is_refused(text):
    with pytest.raises((ValueError, UnicodeError)):
        support_report_bytes(text)


def test_report_larger_than_detail_limit_preserves_original_values():
    text = "Ligne expurgée\n" * 4000 + "Authorization: Bearer PRIVATE_HEADER\n"
    output = support_report_bytes(text).decode()
    assert output.count("Ligne expurgée") == 4000
    assert "PRIVATE_HEADER" in output



def test_json_detail_export_is_type_preserving_indented_bounded_and_private():
    import json
    from cifereca_diagnostics_page import format_diagnostic_detail
    document = {
        "null": None, "zero": 0, "false": False,
        "empty": "", "rows": [None, False, 0, [], {}],
        "unicode": "Élève 🌍 漢字",
        "multiline": "ligne 1\nligne 2",
        "token": "PRIVATE_TOKEN",
        "password": "PRIVATE_PASSWORD",
        "message": "alice@example.com /home/alice/secret",
    }
    encoded = diagnostic_json_bytes(document)
    expected = format_diagnostic_detail(document, "json").encode("utf-8")
    assert encoded == expected and b"\n  " in encoded
    actual = json.loads(encoded)
    assert actual["null"] is None and actual["false"] is False
    assert type(actual["zero"]) is int and actual["zero"] == 0
    assert actual["rows"] == [None, False, 0, [], {}]
    assert actual["multiline"] == "ligne 1\nligne 2"
    assert actual["unicode"] == "Élève 🌍 漢字"
    assert "PRIVATE_PASSWORD" not in encoded.decode()
    assert "mot-de-passe-" in encoded.decode()
    for value in ("PRIVATE_TOKEN", "alice@example.com", "/home/alice"):
        assert value in encoded.decode()
    assert len(encoded) <= 24_000


def json_exporter(value):
    dialog, messages, sensitive = Dialog(), [], []
    gtk = SimpleNamespace(
        FileChooserNative=SimpleNamespace(new=lambda *a: dialog),
        FileChooserAction=SimpleNamespace(SAVE=1),
        ResponseType=SimpleNamespace(ACCEPT=1),
    )
    gio = SimpleNamespace(FileCreateFlags=SimpleNamespace(PRIVATE=1),
                          Cancellable=lambda: SimpleNamespace(cancel=lambda: None))
    exporter = SupportReportExport(
        gtk, gio, SimpleNamespace(set_sensitive=sensitive.append),
        lambda: None, lambda: value, messages.append,
        filename="cifereca-diagnostic.json",
        title="Enregistrer le détail JSON expurgé",
        success_message="Détail JSON expurgé enregistré",
        prepare_bytes=diagnostic_json_bytes,
    )
    return exporter, dialog, messages, sensitive


def test_json_export_uses_local_private_async_write_and_exact_gtk_json():
    import json
    value = {"nested": [0, None, False], "title": "Été", "token": "SECRET"}
    export, dialog, messages, sensitive = json_exporter(value)
    export.choose()
    assert dialog.name == "cifereca-diagnostic.json"
    expected = diagnostic_json_bytes(value)
    assert export.data == expected
    destination = Destination("/tmp/cifereca-diagnostic.json")
    dialog.file = destination
    export.response(dialog, 1)
    assert dialog.destroyed
    assert len(destination.writes) == 1
    data, etag, backup, flags, cancellable, callback, _ = destination.writes[0]
    assert data == expected and json.loads(data)["nested"] == [0, None, False]
    assert "SECRET" in data.decode("utf-8")
    assert backup is False and flags == 1 and etag is None
    assert not messages
    callback(destination, None, None)
    assert messages == ["Détail JSON expurgé enregistré"]
    assert sensitive == [False, True]
    assert export.data is None


def test_json_export_remote_cancel_failure_and_deactivation_are_fail_closed():
    export, dialog, messages, _ = json_exporter({"ok": True})
    export.choose()
    remote = Destination(path=None)
    dialog.file = remote
    export.response(dialog, 1)
    assert remote.writes == []
    assert "fichier local" in messages[0]
    export.choose()
    export.response(dialog, 0)
    assert export.data is None
    export.choose()
    local = Destination()
    dialog.file = local
    export.response(dialog, 1)
    export.close()
    local.callback(local, None, None)
    assert len(messages) == 1
    export.choose()
    assert export.dialog is None


@pytest.mark.parametrize("invalid", ["bad-\ud800"])
def test_json_detail_invalid_unicode_is_never_written_unredacted(invalid):
    encoded = diagnostic_json_bytes({"message": invalid})
    parsed = __import__("json").loads(encoded)
    assert parsed["truncated"] is True
    assert invalid[:4] not in encoded.decode("utf-8")


def test_huge_diagnostic_json_file_uses_renderers_safe_limits():
    import json
    raw = {f"field{i:04d}": "é" * 4000 for i in range(500)}
    result = diagnostic_json_bytes(raw)
    assert len(result) <= 24_000
    decoded = json.loads(result)
    assert decoded.get("truncated") or "[suite]" in str(decoded)
    assert "field0499" not in result.decode()
