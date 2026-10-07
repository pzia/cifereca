from __future__ import annotations

from pathlib import Path

from .profiles import default_output_path, discover_profiles


class _CancelEventAdapter:
    def __init__(self, control):
        self._control = control

    def is_set(self) -> bool:
        return bool(self._control.cancelled())


class OptimizerPage:
    def __init__(self, *, host, context, artifact=None, action_host=None) -> None:
        from gi.repository import Gtk

        self.host = host
        self.context = context
        self.artifact = artifact
        self.action_host = action_host
        self._task_handle = None
        self._closed = False
        self._output_auto = True
        self._setting_output = False
        self._dialogs = []
        self._tasks = context.broker("tasks")
        if self._tasks is None:
            raise RuntimeError("broker tasks indisponible")

        config = dict(getattr(context, "config", {}) or {})
        self.default_profile = str(config.get("default_profile", "device.profile.xteink-x3") or "device.profile.xteink-x3")
        self.max_uncompressed_mb = int(config.get("max_uncompressed_mb", 2048) or 2048)
        self.max_pdf_pages = int(config.get("max_pdf_pages", 5000) or 5000)
        self.max_image_pixels = int(config.get("max_image_pixels", 64_000_000) or 64_000_000)
        self.max_image_mb = int(config.get("max_image_mb", 64) or 64)
        self.profiles = discover_profiles(context)
        self._unsubscribe_profiles = None

        outer = Gtk.ScrolledWindow()
        outer.set_hexpand(True)
        outer.set_vexpand(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)
        outer.set_child(box)
        self.widget = outer

        title = Gtk.Label(label="Optimisation e-ink — EPUB / PDF", xalign=0)
        title.add_css_class("title-2")
        box.append(title)

        intro = Gtk.Label(
            label=(
                "Crée une variante adaptée à une liseuse e-ink sans modifier l'original. "
                "EPUB : structure, texte, CSS et polices conservés ; animations et transparence préservées. "
                "PDF : texte et vectoriel conservés ; seules les images raster compatibles sont remplacées."
            )
        )
        intro.set_wrap(True)
        intro.set_xalign(0)
        box.append(intro)

        if artifact is not None:
            current = Gtk.Label(
                label=(
                    "Livre courant : " + str(getattr(artifact, "filename", "") or getattr(artifact, "artifact_id", "artefact local"))
                    + ". Cifereca gère automatiquement la variante dans la bibliothèque."
                ),
                xalign=0,
            )
            current.set_wrap(True)
            current.add_css_class("accent")
            box.append(current)

        grid = Gtk.Grid(column_spacing=10, row_spacing=10)
        grid.set_hexpand(True)
        box.append(grid)

        grid.attach(self._label(Gtk, "Source EPUB/PDF"), 0, 0, 1, 1)
        self.source_entry = Gtk.Entry()
        self.source_entry.set_hexpand(True)
        self.source_entry.set_placeholder_text("Choisir un fichier .epub ou .pdf")
        grid.attach(self.source_entry, 1, 0, 1, 1)
        source_button = Gtk.Button(label="Choisir…")
        source_button.connect("clicked", self._choose_source)
        grid.attach(source_button, 2, 0, 1, 1)

        grid.attach(self._label(Gtk, "Profil liseuse"), 0, 1, 1, 1)
        self.profile_combo = Gtk.ComboBoxText()
        self._populate_profiles()
        self.profile_combo.connect("changed", self._profile_changed)
        grid.attach(self.profile_combo, 1, 1, 2, 1)

        self.profile_help = Gtk.Label()
        self.profile_help.set_wrap(True)
        self.profile_help.set_xalign(0)
        self.profile_help.add_css_class("dim-label")
        grid.attach(self.profile_help, 1, 2, 2, 1)

        grid.attach(self._label(Gtk, "Sortie"), 0, 3, 1, 1)
        self.output_entry = Gtk.Entry()
        self.output_entry.set_hexpand(True)
        self.output_entry.set_placeholder_text("Fichier de sortie")
        self.output_entry.connect("changed", self._output_changed)
        grid.attach(self.output_entry, 1, 3, 1, 1)
        output_button = Gtk.Button(label="Choisir…")
        output_button.connect("clicked", self._choose_output)
        grid.attach(output_button, 2, 3, 1, 1)

        self.source_button = source_button
        self.output_button = output_button
        if self.artifact is not None:
            self.source_entry.set_text(str(getattr(self.artifact, "filename", "") or getattr(self.artifact, "artifact_id", "Document courant")))
            self.source_entry.set_sensitive(False)
            self.source_button.set_sensitive(False)
            self.output_entry.set_text("Variante gérée par Cifereca")
            self.output_entry.set_sensitive(False)
            self.output_button.set_sensitive(False)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.optimize_button = Gtk.Button(
            label="Obtenir la variante optimisée" if self.artifact is not None else "Créer la variante optimisée"
        )
        self.optimize_button.add_css_class("suggested-action")
        self.optimize_button.connect("clicked", self._start)
        action_row.append(self.optimize_button)
        self.cancel_button = Gtk.Button(label="Annuler")
        self.cancel_button.set_sensitive(False)
        self.cancel_button.connect("clicked", self._cancel_clicked)
        action_row.append(self.cancel_button)
        box.append(action_row)

        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_fraction(0.0)
        self.progress.set_text("Prêt")
        box.append(self.progress)

        self.status = Gtk.Label(label="")
        self.status.set_wrap(True)
        self.status.set_xalign(0)
        self.status.set_selectable(True)
        box.append(self.status)

        note = Gtk.Label(
            label=(
                "Sécurité : EPUB chiffrés refusés ; PDF protégés ou signés refusés. "
                "Si le zoom du profil est inconnu, les tailles CSS/PDF ne servent jamais à réduire "
                "sous la résolution physique de l'écran."
            )
        )
        note.set_wrap(True)
        note.set_xalign(0)
        note.add_css_class("dim-label")
        box.append(note)

        self._refresh_profile_help()
        self._unsubscribe_profiles = context.subscribe_services("device.profile", self._services_changed)

    @staticmethod
    def _label(Gtk, text):
        label = Gtk.Label(label=text)
        label.set_xalign(0)
        return label

    def _parent_window(self):
        try:
            from gi.repository import Gtk
            root = self.widget.get_root()
            return root if isinstance(root, Gtk.Window) else None
        except Exception:
            return None

    def _choose_source(self, _button):
        from gi.repository import Gtk

        dialog = Gtk.FileChooserNative.new(
            "Choisir un EPUB ou PDF",
            self._parent_window(),
            Gtk.FileChooserAction.OPEN,
            "Ouvrir",
            "Annuler",
        )
        file_filter = Gtk.FileFilter()
        file_filter.set_name("EPUB / PDF")
        file_filter.add_pattern("*.epub")
        file_filter.add_pattern("*.EPUB")
        file_filter.add_pattern("*.pdf")
        file_filter.add_pattern("*.PDF")
        dialog.set_filter(file_filter)
        dialog.connect("response", self._source_response)
        self._dialogs.append(dialog)
        dialog.show()

    def _source_response(self, dialog, response):
        from gi.repository import Gtk

        try:
            if response == Gtk.ResponseType.ACCEPT:
                gio_file = dialog.get_file()
                path = gio_file.get_path() if gio_file else None
                if path:
                    self.source_entry.set_text(path)
                    self._output_auto = True
                    self._set_default_output()
        finally:
            dialog.hide()
            if dialog in self._dialogs:
                self._dialogs.remove(dialog)

    def _choose_output(self, _button):
        from gi.repository import Gtk

        dialog = Gtk.FileChooserNative.new(
            "Enregistrer la variante optimisée",
            self._parent_window(),
            Gtk.FileChooserAction.SAVE,
            "Enregistrer",
            "Annuler",
        )
        current = self.output_entry.get_text().strip()
        if current:
            dialog.set_current_name(Path(current).name)
        dialog.connect("response", self._output_response)
        self._dialogs.append(dialog)
        dialog.show()

    def _output_response(self, dialog, response):
        from gi.repository import Gtk

        try:
            if response == Gtk.ResponseType.ACCEPT:
                gio_file = dialog.get_file()
                path = gio_file.get_path() if gio_file else None
                if path:
                    suffix = self._source_suffix()
                    if suffix and not path.casefold().endswith(suffix):
                        path += suffix
                    self._output_auto = False
                    self.output_entry.set_text(path)
        finally:
            dialog.hide()
            if dialog in self._dialogs:
                self._dialogs.remove(dialog)

    def _source_suffix(self) -> str:
        if self.artifact is not None:
            fmt = str(getattr(getattr(self.artifact, "format", None), "format", "") or "").casefold()
            return ".pdf" if fmt == "pdf" else ".epub" if fmt == "epub" else ""
        suffix = Path(self.source_entry.get_text().strip()).suffix.casefold()
        return suffix if suffix in {".epub", ".pdf"} else ""

    def _profile_changed(self, _combo):
        self._refresh_profile_help()
        if self._output_auto:
            self._set_default_output()

    def _populate_profiles(self):
        self.profile_combo.remove_all()
        selected = None
        for plugin_id, profile in self.profiles.items():
            self.profile_combo.append(plugin_id, profile.label)
            if self.default_profile in {plugin_id, profile.id}:
                selected = plugin_id
        if selected is None and self.profiles:
            selected = next(iter(self.profiles))
        if selected is not None:
            self.profile_combo.set_active_id(selected)
        active = self._task_handle is not None and not self._task_handle.done()
        self.optimize_button.set_sensitive(bool(self.profiles) and not active) if hasattr(self, "optimize_button") else None

    def _services_changed(self):
        # Service callbacks must stay short; marshal widget updates to GTK.
        try:
            from gi.repository import GLib
            GLib.idle_add(self._reload_profiles)
        except Exception:
            pass

    def _reload_profiles(self):
        current = self.profile_combo.get_active_id() if hasattr(self, "profile_combo") else None
        self.profiles = discover_profiles(self.context)
        self._populate_profiles()
        if current in self.profiles:
            self.profile_combo.set_active_id(current)
        self._refresh_profile_help()
        return False

    def _output_changed(self, _entry):
        if not self._setting_output:
            self._output_auto = False

    def _refresh_profile_help(self):
        plugin_id = self.profile_combo.get_active_id()
        profile = self.profiles.get(plugin_id) if plugin_id else None
        if profile is None:
            self.profile_help.set_text("Aucun profil device.profile actif.")
            return
        zoom = (
            f"zoom ×{profile.max_zoom_factor:g}"
            if profile.max_zoom_factor is not None
            else "zoom inconnu → mode conservateur"
        )
        self.profile_help.set_text(f"{profile.description} · {zoom}")

    def _set_default_output(self):
        plugin_id = self.profile_combo.get_active_id()
        profile = self.profiles.get(plugin_id) if plugin_id else None
        if profile is None:
            return
        source = (
            str(getattr(self.artifact, "filename", "") or "Livre.epub")
            if self.artifact is not None
            else self.source_entry.get_text().strip()
        )
        if not source:
            return
        try:
            source_path = Path(source)
            if source_path.suffix.casefold() == ".pdf":
                output = source_path.with_name(f"{source_path.stem} [{profile.suffix}].pdf")
            else:
                output = default_output_path(source_path, profile)
        except Exception:
            return
        self._output_auto = True
        self._setting_output = True
        try:
            self.output_entry.set_text(output.name if self.artifact is not None else str(output))
        finally:
            self._setting_output = False

    def _set_busy(self, busy: bool):
        self.optimize_button.set_sensitive(not busy and bool(self.profiles))
        self.cancel_button.set_sensitive(busy)
        self.source_entry.set_sensitive(not busy and self.artifact is None)
        self.output_entry.set_sensitive(not busy and self.artifact is None)
        self.source_button.set_sensitive(not busy and self.artifact is None)
        self.output_button.set_sensitive(not busy and self.artifact is None)
        self.profile_combo.set_sensitive(not busy)

    def _start(self, _button):
        from gi.repository import GLib

        if self._task_handle is not None and not self._task_handle.done():
            return
        plugin_id = self.profile_combo.get_active_id()
        profile = self.profiles.get(plugin_id) if plugin_id else None
        if profile is None or not plugin_id:
            self.host.toast("Choisissez un profil actif")
            return

        suffix = self._source_suffix()
        if suffix not in {".epub", ".pdf"}:
            self.host.toast("Choisissez un fichier EPUB ou PDF")
            return

        self._set_busy(True)
        self.progress.set_fraction(0.0)
        self.progress.set_text("0% — Démarrage…")
        self.status.set_text("")

        artifact_mode = self.artifact is not None
        if artifact_mode:
            operation = lambda control: self._execute_artifact(self.artifact, plugin_id, control)
            label = "Optimisation PDF" if suffix == ".pdf" else "Optimisation EPUB"
        else:
            source = self.source_entry.get_text().strip()
            target = self.output_entry.get_text().strip()
            if not source or not target:
                self._set_busy(False)
                self.host.toast("Choisissez un document et un fichier de sortie")
                return

            def operation(control):
                common = {
                    "profile": profile,
                    "max_image_pixels": self.max_image_pixels,
                    "max_image_mb": self.max_image_mb,
                    "progress": lambda pct, msg: control.report(current=pct, total=100, message=msg),
                    "cancel_event": _CancelEventAdapter(control),
                }
                if suffix == ".pdf":
                    from .pdf_engine import optimize_pdf
                    return optimize_pdf(
                        source,
                        target,
                        max_pages=self.max_pdf_pages,
                        **common,
                    )
                from .engine import optimize_epub
                return optimize_epub(
                    source,
                    target,
                    max_uncompressed_mb=self.max_uncompressed_mb,
                    **common,
                )

            label = "Optimisation PDF locale" if suffix == ".pdf" else "Optimisation EPUB locale"

        def progress(current, total, message):
            if current is None or not total:
                percent = 0
            else:
                percent = max(0, min(100, int((int(current) * 100) / int(total))))
            GLib.idle_add(self._progress_ui, percent, str(message or ""))

        handle = self._tasks.submit(
            operation,
            label=label,
            kind="document-optimizer",
            on_progress=progress,
        )
        self._task_handle = handle
        handle.add_done_callback(lambda done: self._task_done(done, artifact_mode))

    def _cancel_clicked(self, _button):
        if self._task_handle is not None:
            self._task_handle.cancel()
        self.progress.set_text("Annulation…")
        self.cancel_button.set_sensitive(False)

    def _execute_artifact(self, artifact, profile_plugin_id: str, control):
        from cifereca.plugin_api import TransformRequest
        broker = self.context.broker("artifact.execution")
        if broker is None:
            raise RuntimeError("broker artifact.execution indisponible")
        fmt = str(getattr(getattr(artifact, "format", None), "format", "") or "").casefold()
        if fmt == "pdf":
            capability_id = "pdf.image-optimize-eink"
            transformer_id = "document.transformer.pdf-eink-optimizer"
            options = {
                "max_pdf_pages": self.max_pdf_pages,
                "max_image_pixels": self.max_image_pixels,
                "max_image_mb": self.max_image_mb,
            }
        elif fmt == "epub":
            capability_id = "epub.image-optimize-eink"
            transformer_id = "document.transformer.epub-optimizer"
            options = {
                "max_uncompressed_mb": self.max_uncompressed_mb,
                "max_image_pixels": self.max_image_pixels,
                "max_image_mb": self.max_image_mb,
            }
        else:
            raise ValueError("format non supporté")
        request = TransformRequest(
            source=artifact,
            capability_id=capability_id,
            profile_plugin_id=profile_plugin_id,
            options=options,
        )
        return broker.execute_transform(
            request,
            transformer_plugin_id=transformer_id,
            control=control,
        )

    def _task_done(self, handle, artifact_mode: bool):
        from gi.repository import GLib

        if self._closed:
            return
        if handle.control.cancelled():
            GLib.idle_add(self._failure_ui, "Traitement annulé")
            return
        try:
            result = handle.result()
        except Exception as exc:
            self.context.logger.error("Document optimization failed: %s", type(exc).__name__)
            GLib.idle_add(self._failure_ui, f"Échec : {exc}")
        else:
            GLib.idle_add(self._artifact_success_ui if artifact_mode else self._success_ui, result)

    def _progress_ui(self, pct: int, message: str):
        self.progress.set_fraction(pct / 100.0)
        self.progress.set_text(f"{pct}% — {message}" if message else f"{pct}%")
        return False

    def _success_ui(self, report):
        self._task_handle = None
        self._set_busy(False)
        self.progress.set_fraction(1.0)
        self.progress.set_text("100% — Terminé")
        before = _human_size(report.source_bytes)
        after = _human_size(report.output_bytes)
        self.status.set_text(
            f"{report.images_optimized}/{report.images_seen} image(s) optimisée(s). "
            f"{before} → {after} ({report.reduction_percent:.1f}% de réduction).\n"
            f"Sortie : {report.output_path}"
            + (f"\n{len(report.image_errors)} image(s) conservée(s) après erreur de décodage." if report.image_errors else "")
        )
        self.host.toast("Variante optimisée créée")
        return False

    def _artifact_success_ui(self, artifact):
        self._task_handle = None
        self._set_busy(False)
        self.progress.set_fraction(1.0)
        self.progress.set_text("100% — Terminé")
        filename = str(getattr(artifact, "filename", "") or getattr(artifact, "artifact_id", "variante"))
        self.status.set_text(f"Variante disponible dans la bibliothèque : {filename}")
        refresh = getattr(self.action_host, "refresh_context", None)
        if callable(refresh):
            refresh()
        self.host.toast("Variante optimisée disponible dans la bibliothèque")
        return False

    def _failure_ui(self, message: str):
        self._task_handle = None
        self._set_busy(False)
        self.progress.set_fraction(0.0)
        self.progress.set_text("Échec")
        self.status.set_text(message)
        self.host.toast(message)
        return False

    def cancel(self):
        self._closed = True
        if self._task_handle is not None:
            self._task_handle.cancel()
            self._task_handle = None
        unsubscribe = getattr(self, "_unsubscribe_profiles", None)
        if callable(unsubscribe):
            unsubscribe()
            self._unsubscribe_profiles = None
        for dialog in list(self._dialogs):
            try:
                dialog.hide()
            except Exception:
                pass
        self._dialogs.clear()


def _human_size(value: int) -> str:
    size = float(value)
    for unit in ("o", "Kio", "Mio", "Gio"):
        if size < 1024 or unit == "Gio":
            return f"{size:.1f} {unit}" if unit != "o" else f"{int(size)} {unit}"
        size /= 1024
    return f"{value} o"
