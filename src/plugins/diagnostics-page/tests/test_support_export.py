from types import SimpleNamespace

import pytest

from diagnostics_support import SupportReportExport, support_report_bytes


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
    assert "PRIVATE_TOKEN" not in export.data.decode()
    assert "LOAN/private" not in export.data.decode()
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
    assert b"PRIVATE_TOKEN" not in data and flags == 1 and backup is False
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


def test_report_larger_than_detail_limit_is_kept_and_redacted():
    text = "Ligne expurgée\n" * 4000 + "Authorization: Bearer PRIVATE_HEADER\n"
    output = support_report_bytes(text).decode()
    assert output.count("Ligne expurgée") == 4000
    assert "PRIVATE_HEADER" not in output
