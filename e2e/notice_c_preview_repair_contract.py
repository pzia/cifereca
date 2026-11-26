"""#4980: native GTK decode failure after N3 delivery; repair only the preview.

The existing exact public C and scratch cache are reused. The only injected
fault is corruption of the delivered, owned preview immediately before real
Gdk decoding. No production code, real supplier or private account is touched.
"""
from io import BytesIO
import json
import logging
from time import monotonic

from gi.repository import Gdk
from PIL import Image

from cifereca.diagnostics import Diagnostics


def verify_preview_repair(list_cover, detail_cover, *, n2, loader, document,
                          revision, url, pixels, counters, later, guard,
                          capture, report, complete):
    assert list_cover.get_mapped() and detail_cover.get_mapped()
    assert list_cover._request.still_current and detail_cover._request.still_current
    before_n2, before_n3 = n2.stats(), loader.stats()
    before_io = counters()
    source = loader._path(url)
    original = source.read_bytes()
    token = n2.decisions.selected_token(document, revision=revision, uri=url)
    assert token is not None
    fault = []
    original_apply = list_cover._apply_result
    canary = "SYNTHETIC_PRIVATE_DECODE_CANARY"

    class Journal(logging.Handler):
        def __init__(self):
            super().__init__()
            self.entries = []

        def emit(self, record):
            self.entries.append(record.getMessage())
            payload = getattr(record, "_cifereca_journal_payload", None)
            if payload is not None:
                self.entries.append(json.dumps(payload, default=str))

    def corrupt_once(generation, actual_loader, path):
        # Restore immediately: the real repair callback is not intercepted.
        list_cover._apply_result = original_apply
        assert actual_loader is loader and generation == list_cover._generation
        assert list_cover._request.still_current
        assert path is not None and path.parent == loader.preview_root
        assert loader._owned_n3_path(path) and path != source
        assert path.is_file()
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + canary.encode("ascii"))
        logger = logging.getLogger("cifereca")
        old_level, old_diagnostics = logger.level, loader.diagnostics
        journal = Journal()
        diagnostics = Diagnostics(level="detailed")
        logger.addHandler(journal)
        logger.setLevel(logging.DEBUG)
        loader.diagnostics = diagnostics
        try:
            result = original_apply(generation, loader, path)
        finally:
            loader.diagnostics = old_diagnostics
            logger.removeHandler(journal)
            logger.setLevel(old_level)
        rows = diagnostics.snapshot()
        decode = [row for row in rows if row["operation"] == "ui decode"]
        assert len(decode) == 1
        assert decode[0]["detail"] == {"code": "cover_decode_failed"}
        observed = json.dumps(rows) + diagnostics.text("fixture") + "\n".join(journal.entries)
        assert all(secret not in observed for secret in (canary, url, str(path), str(source)))
        assert not path.exists() or path.read_bytes() != b"\x89PNG\r\n\x1a\n" + canary.encode("ascii")
        assert source.read_bytes() == original
        fault.append(path)
        return result

    list_cover._apply_result = corrupt_once
    list_cover.bind_document(n2, document, variant="list", minimum_revision=revision)

    def repaired(deadline):
        texture = list_cover.picture.get_paintable()
        ready = (len(fault) == 1 and isinstance(texture, Gdk.Texture)
                 and not list_cover.placeholder.get_visible()
                 and not list_cover.spinner.get_visible()
                 and list_cover._request.still_current)
        if not ready:
            assert monotonic() < deadline, "native preview repair timeout"
            later(40, guard(lambda: repaired(deadline)))
            return
        assert detail_cover._request.still_current and detail_cover.get_mapped()
        for cover in (list_cover, detail_cover):
            texture = cover.picture.get_paintable()
            assert isinstance(texture, Gdk.Texture) and not cover.placeholder.get_visible()
            with Image.open(BytesIO(texture.save_to_png_bytes().get_data())) as image:
                assert image.convert("RGB").getpixel((0, 0)) == pixels
        after_n2, after_n3 = n2.stats(), loader.stats()
        assert n2.decisions.selected_token(document, revision=revision, uri=url) == token
        assert after_n2["n1_selected_count"] == before_n2["n1_selected_count"]
        assert after_n3["ui_failed"] == before_n3["ui_failed"] + 1
        assert after_n3["decode_failed"] == before_n3["decode_failed"] + 1
        assert after_n3["preview_created"] == before_n3["preview_created"] + 1
        assert after_n3["ui_applied"] == before_n3["ui_applied"] + 1
        assert after_n3["download_ok"] == before_n3["download_ok"]
        assert counters() == before_io and source.read_bytes() == original
        capture("00-notice-c-native-preview-repair")
        report["checks"]["P0_4980_native_preview_decode_repair_redacted"] = True
        complete()

    later(40, guard(lambda: repaired(monotonic() + 8)))
