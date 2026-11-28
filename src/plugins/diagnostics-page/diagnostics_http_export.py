"""HTTP Trace private export controller, using only the public broker."""
from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass


@dataclass(frozen=True, repr=False)
class ScopedHTTPArchive:
    capture_id: str
    scope: object
    capability: str
    evidence_ref: str
    generation: tuple | None = None


class HTTPTraceExport:
    """Own a button, confirmation and native chooser for one page lifetime."""

    LABEL = "Exporter les échanges HTTP…"
    CONFIRM = "Confirmer l’export HTTP non masqué"

    def __init__(self, button, *, broker, tasks, host, current, folder,
                 gtk=None, gio=None, glib=None, include_binary=None,
                 vault=None, archive_source=None, archive_current=None):
        if gtk is None:
            from gi.repository import Gtk, Gio, GLib
            gtk, gio, glib = Gtk, Gio, GLib
        self.gtk, self.gio, self.glib = gtk, gio, glib
        self.button, self.broker, self.tasks, self.host = button, broker, tasks, host
        self.current, self.folder = current, folder
        self.closed = self.armed = False
        self.chooser = self.handle = None
        self.timer = None
        self.include_binary = include_binary
        self.vault, self.archive_source = vault, archive_source
        self.archive_current = archive_current
        self._submission = None
        self._armed_source = self._chosen_source = None
        if archive_source is not None:
            self.LABEL = "Exporter l’archive HTTP sélectionnée…"
        self._updating_policy = False
        if include_binary is not None:
            include_binary.connect("toggled", self.policy_toggled)
        button.connect("clicked", self.clicked)
        self.refresh()
        self.timer = glib.timeout_add(500, self.refresh)

    def _status(self):
        if self.closed or not self.current():
            return {}
        try:
            status = self.broker.http_trace_status()
            return status if isinstance(status, dict) else {}
        except Exception:
            return {}

    def _source(self):
        if self.archive_source is None:
            return None
        sid = self.archive_source()
        policy = self._status().get("include_binary")
        if type(policy) is not bool:
            return None
        if type(sid) is str and sid:
            return sid, policy
        if type(sid) is ScopedHTTPArchive:
            from cifereca.plugin_api.capture_vault import CaptureScope
            if (type(sid.scope) is CaptureScope
                    and all(type(value) is str and bool(value) for value in (
                        sid.capture_id, sid.evidence_ref, sid.scope.provider_id,
                        sid.scope.instance_id, sid.scope.account_id, sid.scope.identity_id))
                    and type(sid.capability) is str
                    and sid.capability in ("search", "detail", "rights", "loans", "personal_library")
                    and callable(self.archive_current)):
                return sid, policy
        return None

    def _archive_still_current(self, source):
        if source is None or self._status().get("include_binary") != source[1]:
            return False
        if type(source[0]) is ScopedHTTPArchive:
            try:
                return self.archive_current(source[0]) is True
            except Exception:
                return False
        return True

    def invalidate(self):
        self.armed = False
        self._armed_source = self._chosen_source = self._submission = None
        self.button.set_label(self.LABEL)
        if self.chooser is not None:
            chooser, self.chooser = self.chooser, None
            chooser.destroy()

    def active(self):
        if self.archive_source is not None:
            return (not self.closed and self.current() and self._source() is not None
                    and callable(getattr(self.vault, "export_http_archive", None)))
        return (callable(getattr(self.broker, 'export_http_trace', None))
                and self._status().get('active') is True)

    def _sync_policy(self, status):
        widget = self.include_binary
        if widget is None:
            return
        value = status.get("include_binary")
        available = (type(value) is bool
                     and callable(getattr(self.broker, "set_http_trace_include_binary", None)))
        self._updating_policy = True
        try:
            widget.set_sensitive(available)
            widget.set_active(value if type(value) is bool else True)
            widget.set_tooltip_text(
                "S’applique à la capture et aux exports HTTP, y compris --capture. "
                "Les corps omis ne peuvent pas être récupérés."
                if available else "Préférence HTTP indisponible sur ce Core."
            )
        finally:
            self._updating_policy = False

    def policy_toggled(self, widget):
        if self._updating_policy or self.closed or not self.current():
            return
        status = self._status()
        setter = getattr(self.broker, "set_http_trace_include_binary", None)
        if type(status.get("include_binary")) is not bool or not callable(setter):
            self._sync_policy(status)
            return
        value = widget.get_active()
        if value == status["include_binary"]:
            return
        self.invalidate()
        try:
            setter(value)
        except Exception:
            self.host.toast("Impossible de modifier la préférence de capture HTTP")
        self.refresh()

    def refresh(self):
        if self.closed or not self.current():
            self.timer = None
            return False
        status = self._status()
        self._sync_policy(status)
        active = (self.active() if self.archive_source is not None else
                  callable(getattr(self.broker, 'export_http_trace', None))
                  and status.get('active') is True)
        if self.archive_source is not None:
            source = self._source()
            if ((self.armed and source != self._armed_source)
                    or ((self.chooser is not None or self._submission is not None)
                        and source != self._chosen_source)):
                self.invalidate()
        self.button.set_sensitive(active)
        self.button.set_tooltip_text(
            "Export local brut : mots de passe, cookies, tokens et URLs signées "
            "peuvent être présents. Ne pas partager sans vérification."
            if active else ("Sélectionnez une archive HTTP locale." if self.archive_source is not None else
                            "Trace inactif ou Core incompatible : activez le niveau "
                            "Trace pour exporter les échanges HTTP de cette session.")
        )
        if not active:
            self.armed = False
            self.button.set_label(self.LABEL)
        return True

    def clicked(self, button):
        if not self.active():
            self.refresh()
            return
        if self.handle is not None and self._submission is not None:
            return
        if not self.armed:
            self._armed_source = self._source()
            if self.archive_source is not None and not self._archive_still_current(self._armed_source):
                self.invalidate()
                return
            self.armed = True
            button.set_label(self.CONFIRM)
            self.host.toast(
                "L’archive HTTP brute peut contenir des credentials : mots de "
                "passe, cookies, tokens et URLs signées. Cliquez à nouveau pour "
                "choisir une destination locale."
            )
            return
        if self.archive_source is not None and (
                self._source() != self._armed_source
                or not self._archive_still_current(self._armed_source)):
            self.invalidate()
            return
        if self.chooser is not None:
            self.host.toast("Un sélecteur d’export HTTP est déjà ouvert")
            return
        self.armed = False
        self._chosen_source = self._armed_source
        button.set_label(self.LABEL)
        root = button.get_root()
        parent = root if isinstance(root, self.gtk.Window) else None
        chooser = self.gtk.FileChooserNative.new(
            self.LABEL, parent, self.gtk.FileChooserAction.SAVE, "Exporter", "Annuler",
        )
        chooser.set_modal(parent is not None)
        folder = self.folder()
        if folder is not None:
            try:
                chooser.set_current_folder(self.gio.File.new_for_path(folder))
            except Exception:
                pass
        chooser.set_current_name(
            "cifereca-echanges-http-" + datetime.now().strftime("%Y%m%d-%H%M%S")
            + ".zip"
        )
        self.chooser = chooser
        chooser.connect("response", self.chose_file)
        chooser.show()

    def chose_file(self, dialog, response):
        try:
            if (self.closed or not self.current() or dialog is not self.chooser
                    or response != self.gtk.ResponseType.ACCEPT):
                return
            if not self.active():
                self.host.toast("Export HTTP indisponible : Trace est désactivé")
                return
            source = self._chosen_source
            if self.archive_source is not None and (
                    self._source() != source or not self._archive_still_current(source)):
                self.invalidate()
                return
            output = dialog.get_file()
            destination = output.get_path() if output is not None else None
            if not destination:
                self.host.toast("Destination locale requise")
                return

            submission = object()
            self._submission = submission

            def write(control):
                if control.cancelled() or self.closed or not self.current():
                    return False
                if self.archive_source is None:
                    self.broker.export_http_trace(destination)
                else:
                    # The worker must not read GTK selection widgets.
                    # Selection changes invalidate the immutable source token
                    # on the GTK thread; the broker reports current policy.
                    if (self._submission is not submission
                            or self._chosen_source != source
                            or not self._archive_still_current(source)):
                        return False
                    selected = source[0]
                    if type(selected) is ScopedHTTPArchive:
                        self.vault.export_http_archive(
                            selected.capture_id, destination, confirmed=True, scope=selected.scope,
                            capability=selected.capability, evidence_ref=selected.evidence_ref,
                            generation=selected.generation)
                    else:
                        self.vault.export_http_archive(selected, destination, confirmed=True)
                return True

            try:
                self.handle = self.tasks.submit(
                    write, label="Export local des échanges HTTP", kind="diagnostics",
                )
            except Exception:
                self._submission = None
                self.host.toast("Export HTTP indisponible")
                return
            self.handle.add_done_callback(self.completed)
        finally:
            if self.chooser is dialog:
                self.chooser = None
            dialog.destroy()

    def completed(self, handle):
        try:
            okay = handle.result() is True
            error = None
        except FileExistsError:
            okay, error = False, "Ce fichier existe déjà : choisissez un nouveau nom"
        except Exception as exc:
            from cifereca.plugin_api.capture_vault import VaultError
            code = exc.args[0] if type(exc) is VaultError and exc.args else None
            okay, error = False, ("Ce fichier existe déjà : choisissez un nouveau nom"
                                  if code == "private_zip_exists" else "Export HTTP impossible")

        def show_status():
            if self.handle is handle:
                self.handle = None
                self._submission = None
            if not self.closed and self.current():
                self.host.toast("Échanges HTTP exportés localement" if okay
                                else error or "Export HTTP annulé")
            return False

        self.glib.idle_add(show_status)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.armed = False
        self._submission = None
        if self.include_binary is not None:
            self.include_binary.set_sensitive(False)
        if self.timer is not None:
            self.glib.source_remove(self.timer)
            self.timer = None
        if self.chooser is not None:
            self.chooser.destroy()
            self.chooser = None
        if self.handle is not None:
            self.handle.cancel()
            self.handle = None


class HTTPVaultArchive:
    """Explicit local archiving; reuse the existing raw export consent controller."""

    def __init__(self, container, *, vault, broker, tasks, host, current, folder,
                 gtk, gio, glib):
        from cifereca.plugin_api.capture_vault import CaptureScope
        self.vault, self.broker, self.tasks, self.host = vault, broker, tasks, host
        self.current, self.glib, self.scope = current, glib, CaptureScope()
        self.closed = False
        self.handle = None
        self.rows = gtk.ComboBoxText()
        self.consent = gtk.CheckButton(
            label="J’autorise le stockage local chiffré des échanges HTTP de cette session."
        )
        self.save = gtk.Button(label="Archiver les échanges HTTP dans le coffre commun")
        self.list = gtk.Button(label="Actualiser les archives HTTP")
        self.export = gtk.Button(label="Exporter l’archive HTTP sélectionnée…")
        self.pin = gtk.Button(label="Épingler l’archive HTTP (30 jours)")
        self.unpin = gtk.Button(label="Retirer l’épingle de l’archive HTTP")
        self.delete_confirm = gtk.CheckButton(label="Je confirme la suppression de l’archive HTTP choisie.")
        self.delete = gtk.Button(label="Supprimer l’archive HTTP")
        self.status = gtk.Label(label="Coffre commun chiffré · archivage volontaire, Trace actif requis.",
                                xalign=0, wrap=True)
        for widget in (self.status, self.consent, self.save, self.list, self.rows,
                       self.export, self.pin, self.unpin, self.delete_confirm, self.delete):
            container.append(widget)
        self.exporter = HTTPTraceExport(
            self.export, broker=broker, tasks=tasks, host=host, current=current, folder=folder,
            gtk=gtk, gio=gio, glib=glib, vault=vault, archive_source=self._selected,
        )
        self.consent.connect("toggled", lambda *_: self._controls())
        self.delete_confirm.connect("toggled", lambda *_: self._controls())
        self.rows.connect("changed", self._selection_changed)
        self.save.connect("clicked", self._archive)
        self.list.connect("clicked", lambda *_: self._list())
        self.pin.connect("clicked", lambda *_: self._pin(True))
        self.unpin.connect("clicked", lambda *_: self._pin(False))
        self.delete.connect("clicked", self._delete)
        self.retention_days = self.retention_mib = self.retention_apply = None
        if (callable(getattr(broker, "capture_retention_policy", None))
                and callable(getattr(broker, "set_capture_retention_policy", None))):
            try:
                policy = broker.capture_retention_policy()
                self.retention_days = gtk.SpinButton.new_with_range(1, 365, 1)
                self.retention_mib = gtk.SpinButton.new_with_range(8, 128, 1)
                self.retention_days.set_value(policy["days"])
                self.retention_mib.set_value(policy["volume_bytes"] / (1024 * 1024))
                self.retention_apply = gtk.Button(label="Appliquer les limites de rétention du coffre")
                for widget in (
                    gtk.Label(label="Rétention automatique : durée en jours", xalign=0),
                    self.retention_days,
                    gtk.Label(label="Volume du coffre en Mio (les épingles restent bornées)", xalign=0),
                    self.retention_mib,
                    self.retention_apply,
                ):
                    container.append(widget)
                self.retention_apply.connect("clicked", self._apply_retention)
            except Exception:
                self.retention_days = self.retention_mib = self.retention_apply = None
        self.private = None
        if all(callable(getattr(broker, name, None)) for name in (
                "provider_health_active_observations", "provider_health_selection_current")
                ) and callable(getattr(vault, "archive_provider_trace", None)):
            self.private = ProviderEvidenceArchive(
                container, vault=vault, broker=broker, tasks=tasks, host=host,
                current=current, folder=folder, gtk=gtk, gio=gio, glib=glib)
        self._controls()

    def _selected(self):
        if self.closed or self.handle is not None:
            return None
        return self.rows.get_active_id()

    def _controls(self):
        idle = not self.closed and self.current() and self.handle is None
        selected = idle and bool(self._selected())
        self.save.set_sensitive(idle and self.consent.get_active())
        self.consent.set_sensitive(idle)
        self.list.set_sensitive(idle)
        self.rows.set_sensitive(idle)
        self.pin.set_sensitive(selected)
        self.unpin.set_sensitive(selected)
        self.delete_confirm.set_sensitive(selected)
        self.delete.set_sensitive(selected and self.delete_confirm.get_active())
        if self.retention_apply is not None:
            self.retention_days.set_sensitive(idle)
            self.retention_mib.set_sensitive(idle)
            self.retention_apply.set_sensitive(idle)
        self.exporter.refresh()

    def _selection_changed(self, *_):
        self.exporter.invalidate()
        self.delete_confirm.set_active(False)
        self._controls()

    def _submit(self, label, work, done):
        if self.closed or not self.current() or self.handle is not None:
            return
        self.exporter.invalidate()
        def execute(control):
            if control.cancelled() or self.closed or not self.current():
                raise RuntimeError("cancelled")
            return work()
        try:
            self.handle = self.tasks.submit(execute, label=label, kind="diagnostics")
        except Exception:
            self.status.set_label("Opération du coffre indisponible.")
            self._controls()
            return
        self._controls()

        def completed(handle):
            try:
                result, okay = handle.result(), True
            except Exception:
                result, okay = None, False
            def deliver():
                if self.closed or not self.current() or self.handle is not handle:
                    return False
                self.handle = None
                if okay:
                    done(result)
                else:
                    self.status.set_label("Opération du coffre refusée : clé, limites ou capture indisponible.")
                self._controls()
                return False
            self.glib.idle_add(deliver)
        self.handle.add_done_callback(completed)

    def _archive(self, *_):
        if not self.consent.get_active():
            return
        self.consent.set_active(False)
        self._submit("Archivage HTTP chiffré",
                     lambda: self.vault.archive_http_trace(consented=True),
                     lambda sid: self._list(preferred=sid))

    def _list(self, *, preferred=None):
        old = self.rows.get_active_id()
        def done(rows):
            self.rows.remove_all()
            ids = []
            for row in rows:
                ids.append(row.capture_id)
                self.rows.append(row.capture_id,
                                 f"{row.saved_at[:16]} · {row.payload_bytes} octets"
                                 + (" · épinglée" if row.pinned_until else ""))
            selected = preferred if preferred in ids else old if old in ids else ids[0] if ids else None
            if selected:
                self.rows.set_active_id(selected)
            self.status.set_label(f"{len(ids)} archives HTTP chiffrées ; aucun envoi.")
        self._submit("Lister les archives HTTP", lambda: self.vault.sessions(self.scope), done)

    def _pin(self, pinned):
        sid = self._selected()
        if sid:
            self._submit("Modifier l’épingle HTTP",
                         lambda: self.vault.pin(self.scope, sid, pinned=pinned, confirmed=True),
                         lambda _: self._list(preferred=sid))

    def _apply_retention(self, *_):
        days = self.retention_days.get_value_as_int()
        volume = self.retention_mib.get_value_as_int() * 1024 * 1024
        self._submit(
            "Configurer la rétention du coffre",
            lambda: self.broker.set_capture_retention_policy(days, volume),
            lambda _: self.status.set_label("Limites de rétention enregistrées ; purge automatique."),
        )

    def _delete(self, *_):
        sid = self._selected()
        if sid and self.delete_confirm.get_active():
            self.delete_confirm.set_active(False)
            self._submit("Supprimer une archive HTTP",
                         lambda: self.vault.delete(self.scope, sid, confirmed=True),
                         lambda _: self._list())

    def close(self):
        self.closed = True
        if self.private is not None:
            self.private.close()
        self.exporter.close()
        if self.handle is not None:
            self.handle.cancel()
            self.handle = None
        self.rows.remove_all()



class ProviderEvidenceArchive:
    """Separate, explicit private observation selection; no provider requests."""

    def __init__(self, container, *, vault, broker, tasks, host, current, folder,
                 gtk, gio, glib):
        self.vault, self.broker, self.tasks = vault, broker, tasks
        self.current, self.glib = current, glib
        self.closed = False
        self.handle = None
        self.tokens = {}
        self.rows = gtk.ComboBoxText()
        self.status = gtk.Label(label="Preuves fournisseurs de l’identité active", xalign=0, wrap=True)
        self.reload = gtk.Button(label="Actualiser les observations fournisseurs")
        self.consent = gtk.CheckButton(label="J’autorise l’archivage chiffré de cette observation.")
        self.save = gtk.Button(label="Archiver la preuve sélectionnée")
        self.export = gtk.Button(label="Exporter la preuve archivée…")
        for widget in (self.status, self.reload, self.rows, self.consent, self.save, self.export):
            container.append(widget)
        self.exporter = HTTPTraceExport(
            self.export, broker=broker, tasks=tasks, host=host, current=current, folder=folder,
            gtk=gtk, gio=gio, glib=glib, vault=vault, archive_source=self._export_source,
            archive_current=self._valid)
        self.rows.connect("changed", self._changed)
        self.consent.connect("toggled", lambda *_: self._controls())
        self.reload.connect("clicked", lambda *_: self.refresh())
        self.save.connect("clicked", self._archive)
        self.refresh()

    def _selected(self):
        if self.closed or not self.current() or self.handle is not None:
            return None
        return self.tokens.get(self.rows.get_active_id())

    def _valid(self, token):
        if self.closed or not self.current() or token is None:
            return False
        try:
            return self.broker.provider_health_selection_current(
                token.scope, token.capability, token.evidence_ref,
                token.capture_id, token.generation) is True
        except Exception:
            return False

    def _export_source(self):
        token = self._selected()
        return token if token is not None and token.capture_id and self._valid(token) else None

    def _controls(self):
        token = self._selected()
        idle = not self.closed and self.current() and self.handle is None
        try:
            trace = self.broker.http_trace_status().get("active") is True
        except Exception:
            trace = False
        allowed = idle and token is not None and not token.capture_id and trace and self._valid(token)
        self.reload.set_sensitive(idle)
        self.rows.set_sensitive(idle)
        self.consent.set_sensitive(allowed)
        self.save.set_sensitive(allowed and self.consent.get_active())
        self.exporter.refresh()

    def _changed(self, *_):
        self.exporter.invalidate()
        self.consent.set_active(False)
        self._controls()

    def refresh(self):
        from cifereca.plugin_api.capture_vault import CaptureScope
        if self.closed or not self.current() or self.handle is not None:
            return
        self.exporter.invalidate()
        self.consent.set_active(False)
        self.tokens.clear()
        self.rows.remove_all()
        try:
            observations = self.broker.provider_health_active_observations()
            for row in observations:
                if not row["evidence_ref"] or not row["generation"]:
                    continue
                scope = CaptureScope(row["provider_id"], row["instance_id"],
                                     row["account_id"], row["identity_id"])
                token = ScopedHTTPArchive(row["capture_id"], scope, row["capability"],
                                          row["evidence_ref"], row["generation"])
                if not self._valid(token):
                    continue
                key = str(len(self.tokens))
                self.tokens[key] = token
                self.rows.append(key, f'{row["provider_id"]} · {row["capability"]} · '
                                      f'{row["observed_at"]} · {row["outcome"]} · '
                                      + ("archivée" if token.capture_id else "non archivée"))
            self.status.set_label(f"{len(self.tokens)} preuves privées disponibles ; aucun envoi.")
        except Exception:
            self.tokens.clear()
            self.rows.remove_all()
            self.status.set_label("Observations fournisseurs indisponibles.")
        self._controls()

    def _archive(self, *_):
        token = self._selected()
        if (token is None or token.capture_id or not self.consent.get_active()
                or not self._valid(token)):
            return
        self.consent.set_active(False)
        self.exporter.invalidate()
        def execute(control):
            if control.cancelled() or not self._valid(token):
                raise RuntimeError("cancelled")
            return self.vault.archive_provider_trace(
                token.scope, token.capability, token.evidence_ref,
                consented=True, generation=token.generation)
        try:
            self.handle = self.tasks.submit(execute, label="Archivage de preuve fournisseur",
                                            kind="diagnostics")
        except Exception:
            self.status.set_label("Archivage indisponible.")
            return
        self._controls()
        def completed(handle):
            try:
                handle.result()
                okay = True
            except Exception:
                okay = False
            def deliver():
                if self.closed or not self.current() or self.handle is not handle:
                    return False
                self.handle = None
                self.refresh()
                if not okay:
                    self.status.set_label("Archivage refusé ou annulé.")
                return False
            self.glib.idle_add(deliver)
        self.handle.add_done_callback(completed)

    def close(self):
        self.closed = True
        self.exporter.close()
        if self.handle is not None:
            self.handle.cancel()
            self.handle = None
        self.tokens.clear()
        self.rows.remove_all()
