"""Developer release-check page, deliberately read-only (P2 #794)."""
from __future__ import annotations

from cifereca.plugin_api import UiPageContribution
from github_releases import (
    DEFAULT_REPOSITORY, ReleaseCheckError, channel_name, fetch_releases,
    installed_version, repository_slug, version_comparison,
)


class DevUpdaterPlugin:
    def __init__(self):
        self.context = None
        self.tasks = None
        self.handle = None
        self.generation = 0

    def activate(self, context):
        self.context = context
        self.tasks = context.broker("tasks")
        if self.tasks is None:
            raise RuntimeError("Broker public tasks requis")
        return self

    def pages(self):
        return [UiPageContribution(
            route="dev-updater",
            title="Releases développeur",
            icon_name="software-update-available-symbolic",
            order=98,
            factory=self.build,
            group="configuration",
        )]

    def build(self, host):
        from gi.repository import Gtk, GLib

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for side in ("top", "bottom", "start", "end"):
            getattr(root, "set_margin_" + side)(16)
        root.append(Gtk.Label(
            label="Vérification manuelle des releases GitHub. Aucun téléchargement, "
                  "aucune installation ni redémarrage depuis cette page.",
            xalign=0, wrap=True,
        ))
        config = dict(self.context.configuration() or self.context.config or {})
        repo = str(config.get("repository") or DEFAULT_REPOSITORY)
        channel = str(config.get("channel") or "stable")
        installed = installed_version()
        current = Gtk.Label(label="Version Cifereca installée : " + (installed or "non disponible"),
                            xalign=0, wrap=True)
        root.append(current)
        origin = Gtk.Label(label=f"Dépôt : {repo} · canal : {channel}", xalign=0, wrap=True)
        root.append(origin)
        check = Gtk.Button(label="Vérifier les releases GitHub")
        check.set_halign(Gtk.Align.START)
        check.set_tooltip_text("Une requête HTTPS explicite à api.github.com ; aucun jeton utilisé")
        root.append(check)
        status = Gtk.Label(label="Vérification non lancée.", xalign=0, wrap=True)
        root.append(status)
        release_title = Gtk.Label(label="", xalign=0, wrap=True)
        release_title.add_css_class("heading")
        root.append(release_title)
        assets = Gtk.Label(label="", xalign=0, wrap=True)
        assets.set_selectable(True)
        root.append(assets)
        notes = Gtk.Label(label="", xalign=0, wrap=True)
        notes.set_selectable(True)
        root.append(notes)

        def check_clicked(_button):
            if self.context is None or self.tasks is None:
                return
            try:
                repository_slug(repo)
                channel_name(channel)
            except ReleaseCheckError as exc:
                status.set_label(str(exc))
                return
            if self.handle is not None and not self.handle.done():
                return
            self.generation += 1
            generation = self.generation
            status.set_label("Recherche des releases…")
            release_title.set_label("")
            assets.set_label("")
            notes.set_label("")
            check.set_sensitive(False)
            handle = self.tasks.submit(
                lambda control: None if control.cancelled() else
                fetch_releases(repository=repo, channel=channel),
                kind="dev-updater", label="Vérification des releases GitHub",
            )
            self.handle = handle

            def complete(done_handle):
                if done_handle.control.cancelled():
                    return
                try:
                    release, error = done_handle.result(), None
                except ReleaseCheckError as exc:
                    release, error = None, str(exc)
                except Exception:
                    release, error = None, "Vérification indisponible."

                def apply():
                    if self.context is None or generation != self.generation:
                        return False
                    self.handle = None
                    check.set_sensitive(True)
                    if error:
                        status.set_label(error)
                    elif release is None:
                        status.set_label("Aucune release publiée dans ce canal (30 dernières examinées).")
                    else:
                        status.set_label(version_comparison(installed, release.tag))
                        release_title.set_label(
                            f"Release : {release.tag} · publiée : {release.published}"
                            + (" · préversion" if release.prerelease else "")
                        )
                        assets.set_label(
                            "Archives ZIP annoncées (non téléchargées, non vérifiées) : "
                            + (", ".join(release.zip_names) if release.zip_names else "aucune")
                            + "\nPreuves publiées signalées (non vérifiées) : "
                            + (", ".join(release.verification_names)
                               if release.verification_names else "aucune")
                        )
                        notes.set_label("Notes de version :\n" +
                                        (release.notes or "Aucune note publiée."))
                    return False

                GLib.idle_add(apply)

            handle.add_done_callback(complete)

        check.connect("clicked", check_clicked)
        return root

    def deactivate(self):
        self.generation += 1
        if self.handle is not None:
            self.handle.cancel()
        self.handle = None
        self.tasks = None
        self.context = None


PLUGIN = DevUpdaterPlugin()
