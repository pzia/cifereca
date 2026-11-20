#!/usr/bin/env python3
"""#2800: native GTK/XTEST handoff to a PREBUILT EPUB Optimizer plugin page.

The selection is an ArtifactRef from the real plugin action, not a pathname.
Only fictitious metadata and a synthetic device profile are used. #773 also
exercises actual EPUB optimization and durable publication in an isolated
temporary SQLite/filesystem via real Core brokers. No provider, real data or
network; this probes GTK/X11 input, not a complete installed shell.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
from hashlib import sha256
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT / "simulation"),
    str(ROOT / "src/plugins/epub_optimizer"),
    str(ROOT / "environnements/openai-chat-debian"),
]

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from cifereca.plugin_api import ArtifactFormat, ArtifactRef, UiActionContext
from cifereca_epub_optimizer.builtin_profiles import builtin_profile
from cifereca_epub_optimizer_plugin import EpubOptimizerPlugin
from x11_input import X11Input


def artifact(identifier: str, fmt: str):
    return ArtifactRef(
        "synthetic-" + identifier, "synthetic-book-" + identifier,
        ArtifactFormat(
            "application/pdf" if fmt == "pdf" else "application/epub+zip",
            fmt, "none",
        ),
        128, "a" * 64,
        filename="/PRIVATE/not-an-account/" + identifier + "." + fmt,
    )



def exercise_publication(controller, context, select, click, until, report):
    """Native user clicks; real task broker, transformer and writer, no real data."""
    from cifereca.artifact_runtime import ArtifactExecutionBrokerImpl
    from cifereca.core.derived_variant_journal import summarize_derived_journal
    from cifereca.storage import Storage
    from cifereca.task_runtime import TaskBrokerImpl
    from cifereca_epub_optimizer_transformer import EpubOptimizerTransformer
    from plugin_scenarios import create

    with TemporaryDirectory(prefix="cifereca-p773-") as tmp:
        root = Path(tmp).resolve()
        dataset = root / "dataset"
        manifest = create(dataset)
        library = root / "library"
        identity = "synthetic-identity"
        folder = library / identity
        folder.mkdir(parents=True)
        storage = Storage(root / "state.sqlite")
        transformer_id = "document.transformer.epub-optimizer"
        profile_id = "device.profile.xteink-x3"
        transformer = EpubOptimizerTransformer()
        transformer.activate(context)
        resolved = NS(plugin_id=transformer_id, service=transformer,
                      registry_generation=1, capabilities=("artifact.plan", "artifact.transform"))
        manager = NS(
            services=lambda category: (resolved,) if category == "document.transformer" else (),
            statuses=lambda: tuple(NS(descriptor=NS(id=key, version="1"))
                                   for key in (transformer_id, profile_id)),
        )
        service = NS(storage=storage, config={"library.directory": str(library)},
                     current_identity=NS(identity_id=identity), plugin_manager=manager)
        broker = ArtifactExecutionBrokerImpl(service)
        # Feature flag exclusively in this synthetic process.
        assert not broker._durable_variant_publication
        broker._durable_variant_publication = True
        task_broker = TaskBrokerImpl(service, max_workers=1)
        controller._tasks = task_broker.for_plugin("ui.page.epub-optimizer")
        old_broker = context.broker
        context.broker = lambda name: (broker if name == "artifact.execution" else
                                       controller._tasks if name == "tasks" else None)
        originals = {}

        def register(filename):
            contents = (dataset / filename).read_bytes()
            path = folder / filename
            path.write_bytes(contents)
            digest = sha256(contents).hexdigest()
            assert digest == manifest["files"][filename]
            aid = "sha256:" + digest
            media = "application/epub+zip"
            storage.artifact_put({"artifact_id": aid, "sha256": digest,
                                  "size": len(contents), "media_format": "epub",
                                  "mime_type": media})
            pid = storage.placement_put({"artifact_id": aid, "identity_id": identity,
                                         "path": str(path), "state": "present"})
            originals[path] = contents
            return ArtifactRef(aid, "book-" + filename, ArtifactFormat(media, "epub", "none"),
                               len(contents), digest, filename=filename,
                               identity_id=identity, placement_id=pid)

        def no_derived():
            return (len(storage.placements()) == 2
                    and not storage.artifact_variants()
                    and summarize_derived_journal(storage)["count"] == 0)

        gate, entered = Event(), Event()
        blocker = None
        try:
            with patch("cifereca.paths.acquisition_lock_file",
                       lambda: root / "acquisition.lock"):
                storage.identity_put({"identity_id": identity, "label": identity,
                                      "directory_name": identity})
                valid = register("optimizer-valid.epub")
                corrupt = register("optimizer-corrupt.epub")
                report["phase"] = "generated_cancel"
                select(valid)
                def block(control):
                    entered.set()
                    assert gate.wait(15), "synthetic_gate_timeout"
                    control.check_cancelled()

                # Keep the real single-worker executor occupied, so Cancel
                # deterministically wins before the admitted operation runs.
                blocker = task_broker.submit(block)
                until(entered.is_set, "cancel_gate")
                click(controller.optimize_button)
                until(lambda: controller._task_handle is not None
                      and controller.cancel_button.get_sensitive(), "cancel_busy")
                click(controller.cancel_button)
                gate.set()
                blocker.result(timeout=8)
                until(lambda: controller._task_handle is None, "cancel_completion")
                report["checks"]["generated_cancel_keeps_original"] = (
                    controller.status.get_text() == "Traitement annulé"
                    and not controller.cancel_button.get_sensitive()
                    and no_derived()
                )
                assert report["checks"]["generated_cancel_keeps_original"]

                report["phase"] = "generated_corrupt"
                select(corrupt)
                click(controller.optimize_button)
                until(lambda: controller._task_handle is None
                      and controller.progress.get_text() == "Échec", "corrupt_completion")
                report["checks"]["generated_corrupt_refuses_publication"] = (
                    controller.status.get_text().startswith("Échec de l’optimisation")
                    and no_derived()
                )
                assert report["checks"]["generated_corrupt_refuses_publication"]

                report["phase"] = "generated_success"
                select(valid)
                started = time.monotonic()
                click(controller.optimize_button)
                until(lambda: controller._task_handle is None
                      and controller.progress.get_text() == "100% — Terminé", "success_completion")
                report["checks"]["generated_epub_success_rendered"] = (
                    controller.status.get_text().startswith("Variante disponible dans la bibliothèque")
                    and not controller.cancel_button.get_sensitive()
                    and controller.optimize_button.get_sensitive()
                    and time.monotonic() - started < 9
                )
                assert report["checks"]["generated_epub_success_rendered"]
                variant, = storage.artifact_variants()
                child, = [row for row in storage.placements()
                          if row["artifact_id"] == variant["artifact_id"]]
                output = Path(child["path"])
                report["checks"]["generated_derived_exact_publication"] = (
                    child["identity_id"] == identity and output.parent == folder
                    and child["artifact_id"] == "sha256:" + sha256(output.read_bytes()).hexdigest()
                    and variant["source_artifact_id"] == valid.artifact_id
                    and summarize_derived_journal(storage)["committed_count"] == 1
                    and summarize_derived_journal(storage)["pending_count"] == 0
                )
                assert report["checks"]["generated_derived_exact_publication"]
                with storage._lock, storage._connect() as sql:
                    origin, = sql.execute(
                        "SELECT origin_kind,identity_id,artifact_id,source_reference_id,"
                        "parent_artifact_id,metadata_json FROM origin_links"
                    ).fetchall()
                    report["checks"]["generated_no_provider_right"] = (
                        origin[:5] == ("derived", identity, child["artifact_id"], None, valid.artifact_id)
                        and json.loads(origin[5])["source_placement_id"] == valid.placement_id
                        and sql.execute("SELECT count(*) FROM source_references").fetchone() == (0,)
                        and sql.execute("SELECT count(*) FROM entitlements").fetchone() == (0,)
                    )
                assert report["checks"]["generated_no_provider_right"]
                report["checks"]["generated_originals_intact"] = all(
                    path.read_bytes() == contents and
                    (dataset / path.name).read_bytes() == contents
                    for path, contents in originals.items()
                )
                assert report["checks"]["generated_originals_intact"]
                report["synthetic_publications"] = 1
        finally:
            gate.set()
            handle = controller._task_handle
            if handle is not None:
                handle.cancel()
                try:
                    handle.result(timeout=8)
                except Exception:
                    pass
            if blocker is not None:
                try:
                    blocker.result(timeout=8)
                except Exception:
                    pass
            task_broker.close()
            context.broker = old_broker
            transformer.deactivate()
            storage.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, choices=(1366, 1600), required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("Real X11 GTK/XTEST display required")

    report = {
        "scenario": "optimizer-handoff-gtk",
        "status": "RUNNING",
        "backend": "GTK4/X11/XTEST",
        "width": args.width,
        "phase": "setup",
        "checks": {},
        "network_calls": 0,
        "writes": 0,
    }
    def save():
        (out / "screen.json").write_text(
            json.dumps(report, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )

    Gtk.init()
    device = X11Input()
    window = Gtk.Window(title="Cifereca synthetic optimizer handoff")
    window.set_default_size(args.width - 80, 800)
    profile = builtin_profile("xteink-x3")
    tasks = NS()  # No task during the handoff-only first phase.
    logger = logging.getLogger("synthetic-optimizer-gtk")
    context = NS(
        config={},
        logger=logger,
        report_diagnostic=lambda *_args, **_kwargs: None,
        broker=lambda name: tasks if name == "tasks" else None,
        services=lambda *args, **kwargs: (
            NS(plugin_id="device.profile.xteink-x3",
               plugin_version="0.3.7", service=profile),
        ),
        subscribe_services=lambda *args, **kwargs: lambda: None,
    )
    plugin = EpubOptimizerPlugin()
    plugin.activate(context)
    selected = [None]
    navs, toasts = [], []

    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    for child in (root, nav):
        child.set_margin_start(8)
        child.set_margin_end(8)
    root.append(nav)
    stack = Gtk.Stack()
    stack.set_hexpand(True)
    stack.set_vexpand(True)
    root.append(stack)

    class Host:
        def action_context(self):
            return UiActionContext(
                target="book.detail", artifact=selected[0],
                identity_id="synthetic-identity",
            )
        def navigate(self, route):
            navs.append(route)
            stack.set_visible_child_name(route)
        def toast(self, message):
            toasts.append("notice")

    host = Host()
    catalog = Gtk.Label(label="Bibliothèque — uniquement deux livres fictifs")
    stack.add_named(catalog, "library")
    plugin_page = plugin._build_page(host)
    controller = plugin._active_controller
    assert controller is not None and controller.artifact is None
    stack.add_named(plugin_page, "epub-optimize")
    action = next(a for a in plugin.pages()[0].actions
                  if a.id == "epub-optimize-current")
    a = artifact("SyntheticA", "epub")
    b = artifact("SyntheticB", "pdf")
    def button(text, callback):
        widget = Gtk.Button(label=text)
        widget.connect("clicked", lambda *_: callback())
        nav.append(widget)
        return widget

    back = button("Retour Bibliothèque",
                  lambda: stack.set_visible_child_name("library"))
    button_a = button("Optimiser A",
                      lambda: (selected.__setitem__(0, a), action.callback(host)))
    button_b = button("Optimiser B",
                      lambda: (selected.__setitem__(0, b), action.callback(host)))
    direct = button("Ouvrir page Optimiseur",
                    lambda: host.navigate("epub-optimize"))
    stack.set_visible_child_name("library")
    window.set_child(root)
    window.present()

    def until(predicate, label):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            if predicate():
                return
            time.sleep(.025)
        raise AssertionError("native_" + label + "_not_observed")

    def click(widget):
        until(lambda: widget.get_mapped(), "widget_mapping")
        device.click_widget(widget)
        for _ in range(8):
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            time.sleep(.02)

    try:
        until(lambda: window.get_mapped() and plugin_page.get_mapped() is False,
              "initial_library")
        report["checks"]["prebuilt_blank_page"] = (
            controller.artifact is None
            and not controller._artifact_info_label.get_visible()
        )
        assert report["checks"]["prebuilt_blank_page"]

        report["phase"] = "first_selection"
        click(button_a)
        until(lambda: controller.artifact is a and
              stack.get_visible_child_name() == "epub-optimize", "select_a")
        report["checks"]["action_a_to_prebuilt_page"] = (
            controller.source_entry.get_text() == "SyntheticA.epub"
            and controller.action_host is host
            and controller._artifact_info_label.get_visible()
            and not controller.source_button.get_sensitive()
        )
        assert report["checks"]["action_a_to_prebuilt_page"]

        report["phase"] = "second_selection_after_back"
        click(back)
        until(lambda: stack.get_visible_child_name() == "library", "back")
        click(button_b)
        until(lambda: controller.artifact is b and
              stack.get_visible_child_name() == "epub-optimize", "select_b")
        report["checks"]["action_b_replaces_a_without_rebuild"] = (
            plugin._active_controller is controller
            and controller.source_entry.get_text() == "SyntheticB.pdf"
            and controller.optimize_button.get_label() == "Obtenir la variante optimisée"
            and controller._artifact_info_label.get_visible()
            and not controller.source_entry.get_sensitive()
        )
        assert report["checks"]["action_b_replaces_a_without_rebuild"]

        report["phase"] = "busy_refusal"
        click(back)
        controller._task_handle = NS(done=lambda: False)
        click(button_a)
        report["checks"]["busy_refuses_stale_handoff"] = (
            controller.artifact is b
            and stack.get_visible_child_name() == "library"
            and len(toasts) == 1
        )
        assert report["checks"]["busy_refuses_stale_handoff"]
        controller._task_handle = None

        report["phase"] = "reentry"
        click(button_a)
        until(lambda: controller.artifact is a and
              stack.get_visible_child_name() == "epub-optimize", "reentry")
        report["checks"]["reentry_after_busy_uses_latest_book"] = (
            controller.source_entry.get_text() == "SyntheticA.epub"
            and controller.progress.get_text() == "Prêt"
        )
        assert report["checks"]["reentry_after_busy_uses_latest_book"]

        visible = (
            controller._artifact_info_label.get_label()
            + controller.source_entry.get_text()
            + controller.output_entry.get_text()
        )
        report["checks"]["no_private_parent_path_in_widgets"] = (
            "PRIVATE" not in visible and "not-an-account" not in visible
            and "/PRIVATE/" not in visible
        )
        assert report["checks"]["no_private_parent_path_in_widgets"]
        report["checks"]["no_optimization_started"] = (
            controller._task_handle is None and not controller.cancel_button.get_sensitive()
        )
        assert report["checks"]["no_optimization_started"]
        report["checks"]["two_exact_public_navigation_calls"] = (
            navs == ["epub-optimize"] * 3
        )
        assert report["checks"]["two_exact_public_navigation_calls"]

        def select(ref):
            nonlocal a
            a = ref
            click(back)
            click(button_a)
            until(lambda: controller.artifact is ref and
                  stack.get_visible_child_name() == "epub-optimize", "generated_selection")

        exercise_publication(controller, context, select, click, until, report)
        report["status"] = "PASS"
        report["phase"] = "complete"
    except Exception:
        report["status"] = "FAIL"
        # Only closed phase and boolean checks enter the report; never emit
        # native exceptions (which can contain paths) into CI artifacts.
        save()
        raise AssertionError("optimizer_native_handoff_failed") from None
    finally:
        # The synthetic task is not an active Core TaskHandle.
        controller._task_handle = None
        plugin.deactivate()
        device.close()
        window.destroy()
    save()
    print("optimizer-native-handoff: PASS", flush=True)


if __name__ == "__main__":
    main()
