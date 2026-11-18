"""#2800: the prebuilt optimizer page receives the exact selected ArtifactRef."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.plugin_api import ArtifactFormat, ArtifactRef, UiActionContext
from cifereca_epub_optimizer_plugin import EpubOptimizerPlugin
from cifereca_epub_optimizer.ui import OptimizerPage


def _artifact(ident, fmt="epub", drm="none", filename=None):
    return ArtifactRef(
        ident, "book-" + ident,
        ArtifactFormat("application/pdf" if fmt == "pdf"
                       else "application/epub+zip", fmt, drm),
        123, "a" * 64, filename=filename or ident + "." + fmt,
    )


class Host:
    def __init__(self, selected):
        self.selected = selected
        self.routes = []
        self.toasts = []
    def action_context(self):
        return UiActionContext(
            target="book.detail", artifact=self.selected,
            identity_id="fixture-user",
        )
    def navigate(self, route):
        self.routes.append(route)
    def toast(self, message):
        self.toasts.append(message)


class MountedPage:
    _closed = False
    def __init__(self):
        self.selections = []
        self.busy = False
    def open_artifact(self, artifact, *, action_host):
        if self.busy:
            return False
        self.selections.append((artifact, action_host))
        return True


def test_prebuilt_page_receives_a_then_b_at_click_time_without_rebuilding():
    plugin = EpubOptimizerPlugin()
    page = MountedPage()
    plugin._active_controller = page
    a, b = _artifact("A"), _artifact("B", fmt="pdf")
    host_a, host_b = Host(a), Host(b)
    assert plugin._open_from_detail(host_a) is a
    assert plugin._open_from_detail(host_b) is b
    assert page.selections == [(a, host_a), (b, host_b)]
    assert host_a.routes == ["epub-optimize"]
    assert host_b.routes == ["epub-optimize"]
    assert plugin._pending_artifact is None
    assert plugin._pending_action_host is None
    plugin.deactivate()


def test_busy_mounted_page_rejects_new_context_instead_of_reusing_old():
    plugin = EpubOptimizerPlugin()
    page = MountedPage()
    page.busy = True
    plugin._active_controller = page
    host = Host(_artifact("B"))
    assert plugin._open_from_detail(host) is None
    assert host.routes == [] and len(host.toasts) == 1
    assert page.selections == []
    assert plugin._pending_artifact is None


def test_initial_factory_fallback_stays_only_before_page_is_built():
    plugin = EpubOptimizerPlugin()
    a = _artifact("A")
    host = Host(a)
    assert plugin._open_from_detail(host) is a
    assert plugin._pending_artifact is a
    assert plugin._pending_action_host is host


def test_protected_or_wrong_format_is_rejected_before_navigation():
    plugin = EpubOptimizerPlugin()
    page = MountedPage()
    plugin._active_controller = page
    for a in (_artifact("A", drm="adept"), _artifact("B", fmt="mobi")):
        host = Host(a)
        assert plugin._open_from_detail(host) is None
        assert host.toasts and host.routes == []
    assert page.selections == []


class Widget:
    def __init__(self):
        self.text = ""
        self.label = ""
        self.sensitive = None
        self.visible = False
    def set_text(self, text):
        self.text = text
    def set_label(self, value):
        self.label = value
    def set_visible(self, value):
        self.visible = value
    def set_sensitive(self, value):
        self.sensitive = value
    def set_fraction(self, value):
        self.fraction = value


def _page():
    page = OptimizerPage.__new__(OptimizerPage)
    page._closed = False
    page._task_handle = None
    page._run_token = None
    page._set_busy = lambda _busy: None
    page.artifact = None
    page.action_host = None
    page._invalidated = 0
    page._invalidate_semantic_preview = lambda: setattr(
        page, "_invalidated", page._invalidated + 1,
    )
    page._artifact_info_label = Widget()
    page.source_entry = Widget()
    page.source_button = Widget()
    page.output_entry = Widget()
    page.output_button = Widget()
    page.optimize_button = Widget()
    page.progress = Widget()
    page.status = Widget()
    page._default_output = []
    page._set_default_output = lambda: page._default_output.append(
        page.artifact.artifact_id,
    )
    return page


def test_live_optimizer_switches_source_filename_and_core_lease_without_path():
    page = _page()
    a = _artifact("A", filename="/private/account/A.epub")
    b = _artifact("B", fmt="pdf", filename="/private/account/B.pdf")
    assert page.open_artifact(a, action_host=Host(a)) is True
    assert page.source_entry.text == "A.epub"
    assert page._artifact_info_label.visible
    assert page.open_artifact(b, action_host=Host(b)) is True
    assert page.artifact is b
    assert page.source_entry.text == "B.pdf"
    assert page._artifact_info_label.label.endswith("Fichier et variante résolus par Cifereca.")
    assert "/private/account" not in page._artifact_info_label.label
    assert page.source_entry.sensitive is False
    assert page.source_button.sensitive is False
    assert page.output_entry.sensitive is False
    assert page.output_button.sensitive is False
    assert page.optimize_button.label == "Obtenir la variante optimisée"
    assert page._default_output == ["A", "B"]
    assert page._invalidated == 2
    assert page.progress.text == "Prêt"

    seen = []
    page.max_pdf_pages = 300
    page.max_image_pixels = 4000
    page.max_image_mb = 12
    class Broker:
        def execute_transform(self, request, *, transformer_plugin_id, control):
            seen.append((request, transformer_plugin_id, control))
            return "fake"
    page.context = NS(broker=lambda ident: Broker() if ident == "artifact.execution"
                      else None)
    token = object()
    assert page._execute_artifact(page.artifact, "device.profile.test", token) == "fake"
    assert seen[0][0].source is b
    assert seen[0][0].capability_id == "pdf.image-optimize-eink"
    assert seen[0][1] == "document.transformer.pdf-eink-optimizer"
    assert seen[0][2] is token


def test_running_optimizer_never_overwrites_the_active_source():
    page = _page()
    a, b = _artifact("A"), _artifact("B")
    assert page.open_artifact(a)
    page._task_handle = NS(done=lambda: False)
    assert page.open_artifact(b) is False
    assert page.artifact is a
    assert page.source_entry.text == "A.epub"
    page._task_handle = NS(done=lambda: True)
    assert page.open_artifact(b) is True
    page._closed = True
    assert page.open_artifact(a) is False
    assert page.artifact is b


def test_context_rejects_stale_or_protected_refs_without_changing_source():
    page = _page()
    a = _artifact("A")
    assert page.open_artifact(a)
    for invalid in (None, _artifact("B", drm="lcp"), _artifact("C", fmt="mobi")):
        assert not page.open_artifact(invalid)
        assert page.artifact is a


def test_deferred_optimization_captures_request_and_drops_stale_completion(monkeypatch):
    """Real start/open callbacks, pending and idle queues independently driven."""
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace

    idle, jobs, seen, foreign, refreshes = [], [], [], [], []
    glib = NS(idle_add=lambda callback, *args: idle.append((callback, args)))
    monkeypatch.setitem(sys.modules, "gi", NS(repository=NS(GLib=glib)))
    monkeypatch.setitem(sys.modules, "gi.repository", NS(GLib=glib))

    class Handle:
        def __init__(self, operation, progress):
            self.operation, self.progress = operation, progress
            self.finished = False
            self.control = NS(cancelled=lambda: False)
        def done(self):
            return self.finished
        def result(self):
            assert self.finished
            return self.value
        def add_done_callback(self, callback):
            self.callback = callback
        def run(self, pool):
            self.progress(50, 100, "old progress")
            self.value = pool.submit(self.operation, self.control).result()
            self.finished = True
            self.callback(self)

    def submit(operation, *, on_progress, **kwargs):
        handle = Handle(operation, on_progress)
        jobs.append(handle)
        return handle

    class Broker:
        def __init__(self, calls):
            self.calls = calls
        def execute_transform(self, request, *, transformer_plugin_id, control):
            self.calls.append((request, transformer_plugin_id))
            return replace(request.source, filename="variant.epub")

    page = _page()
    page.host = Host(None)
    page._tasks = NS(submit=submit)
    page.profiles = {"profile": object()}
    page.profile_combo = NS(get_active_id=lambda: "profile")
    page._source_suffix = lambda: "." + page.artifact.format.format
    page.max_uncompressed_mb = 300
    page.max_pdf_pages = 400
    page.max_image_pixels = 5000
    page.max_image_mb = 12
    logger = NS(error=lambda *_: None)
    original = Broker(seen)
    page.context = NS(broker=lambda _: original, logger=logger)
    page._progress_ui = lambda *args: refreshes.append(("progress", args))

    def success(result):
        page._task_handle = None
        refreshes.append((page.action_host, result.identity_id))

    page._artifact_success_ui = success
    a = replace(_artifact("A"), identity_id="alice", placement_id="copy-a")
    b = replace(_artifact("B"), identity_id="bob", placement_id="copy-b")
    host_a, host_b = Host(a), Host(b)
    assert page.open_artifact(a, action_host=host_a)
    page._start(None)
    first, = jobs
    assert not page.open_artifact(b, action_host=host_b)

    # The task must use the admitted request and bound broker, not page
    # attributes. A normal open_artifact cannot change them mid-flight.
    page.max_image_pixels = 999
    replacement = Broker(foreign)
    page.context = NS(broker=lambda _: replacement, logger=logger)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first.run(pool)
        assert first.done()
        request, transformer = seen[0]
        assert request.source is a
        assert (request.identity_id, request.placement_id) == ("alice", "copy-a")
        assert request.options["max_image_pixels"] == 5000
        assert transformer == "document.transformer.epub-optimizer"
        assert foreign == []
        assert page.open_artifact(b, action_host=host_b)
        page._start(None)
        second = jobs[-1]
        assert second is not first
        old, idle[:] = idle[:], []
        for callback, args in old:
            callback(*args)
        assert refreshes == []
        assert page._task_handle is second
        second.run(pool)
        assert foreign[0][0].source is b
        for callback, args in idle:
            callback(*args)
        assert refreshes[-1] == (host_b, "bob")
        assert page._task_handle is None
