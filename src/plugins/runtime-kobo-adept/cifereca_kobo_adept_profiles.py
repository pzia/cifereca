from __future__ import annotations

from cifereca.plugin_api import UiPageContribution


def _run_once_on_first_map(widget, callback):
    """Run a callback once, and only when the page is effectively mapped."""
    fired = False

    def on_map(_widget):
        nonlocal fired
        if fired:
            return
        fired = True
        callback()

    widget.connect("map", on_map)
    get_mapped = getattr(widget, "get_mapped", None)
    if callable(get_mapped) and get_mapped():
        on_map(widget)


def _run_profile_task(runtime, fn, *, reload_profiles: bool):
    """Run runtime work entirely off the GTK thread and return renderable profiles."""
    value = fn()
    profiles = runtime.profiles() if reload_profiles else value
    return value, profiles


class AdeptProfilesPagePlugin:
    def __init__(self):
        self.context=None
        self.runtime=None
        self.tasks=None
        self._task_handles=[]

    def activate(self,context):
        self.context=context
        self.runtime=context.service("runtime.kobo-adept")
        self.tasks=context.broker("tasks")
        if self.runtime is None:
            raise RuntimeError("runtime.kobo-adept indisponible")
        if self.tasks is None:
            raise RuntimeError("broker tasks indisponible")
        return self

    def pages(self):
        return [UiPageContribution(
            route="adept-profiles", title="Profils Adobe",
            icon_name="avatar-default-symbolic", order=82,
            factory=self._build, group="configuration",
        )]

    def _build(self,host):
        from gi.repository import GLib, Gtk

        scroll=Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
        for margin in ("top","bottom","start","end"):
            getattr(box,"set_margin_"+margin)(18)
        scroll.set_child(box)

        title=Gtk.Label(label="Profils Adobe / ADEPT",xalign=0)
        title.add_css_class("page-title"); box.append(title)
        intro=Gtk.Label(label="Créer un profil ADEPT utilisable pour les ACSM, puis associer une liseuse déjà autorisée avec la même identité Adobe.",xalign=0)
        intro.set_wrap(True); box.append(intro)

        profiles=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6)
        box.append(profiles)

        name=Gtk.Entry(); name.set_placeholder_text("Nom du profil")
        email=Gtk.Entry(); email.set_placeholder_text("Adresse Adobe (email)")
        mount=Gtk.Entry(); mount.set_placeholder_text("Point de montage de la liseuse")
        box.append(name); box.append(email); box.append(mount)

        actions=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
        refresh=Gtk.Button(label="Actualiser")
        create=Gtk.Button(label="Créer / activer")
        associate=Gtk.Button(label="Associer la liseuse")
        remove=Gtk.Button(label="Supprimer")
        actions.append(refresh); actions.append(create); actions.append(associate); actions.append(remove); box.append(actions)
        confirm=Gtk.CheckButton(label="Confirmer explicitement la suppression du profil saisi")
        box.append(confirm)
        remove.set_sensitive(False)
        confirm.connect("toggled",lambda button:remove.set_sensitive(button.get_active()))

        buttons=(refresh,create,associate,remove)

        def clear(container):
            child=container.get_first_child()
            while child is not None:
                nxt=child.get_next_sibling(); container.remove(child); child=nxt

        def set_busy(value):
            for button in buttons: button.set_sensitive(not value)
            if not value: remove.set_sensitive(confirm.get_active())

        def render(payload):
            clear(profiles)
            rows=payload.get("profiles",[]) if isinstance(payload,dict) else []
            if not rows:
                empty=Gtk.Label(label="Aucun profil ADEPT.",xalign=0); profiles.append(empty); return
            for row in rows:
                if not isinstance(row,dict): continue
                profile_name=str(row.get("name") or row.get("profile") or "")
                provisioning=row.get("provisioning") if isinstance(row.get("provisioning"),dict) else {}
                ready=row.get("complete") is True and row.get("fetch_capable") is True and provisioning.get("acsm_fetch_ready") is True
                devices=row.get("devices") if isinstance(row.get("devices"),list) else []
                button=Gtk.Button(label=f"{profile_name} · {'prêt ACSM' if ready else 'incomplet'} · {len(devices)} liseuse(s)")
                button.set_halign(Gtk.Align.FILL)
                button.connect("clicked",lambda _b,value=profile_name:name.set_text(value))
                profiles.append(button)

        def async_call(fn,success_message=None,*,reload_profiles=False):
            set_busy(True)
            handle=self.tasks.submit(
                lambda _control:_run_profile_task(self.runtime,fn,reload_profiles=reload_profiles),
                label="Gestion profil ADEPT",kind="adept-profile")
            self._task_handles.append(handle)

            def finished(done_handle):
                try:
                    value,profile_payload=done_handle.result(); error=None
                except Exception as exc:
                    value=None; profile_payload=None; error=exc

                def done():
                    if done_handle in self._task_handles:
                        self._task_handles.remove(done_handle)
                    if self.context is None:
                        return False
                    set_busy(False)
                    if error is not None:
                        host.toast(str(error)[:240])
                    else:
                        if success_message: host.toast(success_message)
                        render(profile_payload)
                    return False
                GLib.idle_add(done)

            handle.add_done_callback(finished)

        refresh.connect("clicked",lambda _b:async_call(self.runtime.profiles))
        create.connect("clicked",lambda _b:async_call(
            lambda:self.runtime.provision_profile(name.get_text(),email.get_text()),
            "Profil ADEPT prêt",reload_profiles=True))
        associate.connect("clicked",lambda _b:async_call(
            lambda:self.runtime.associate_profile(name.get_text(),mount.get_text()),
            "Liseuse associée",reload_profiles=True))
        def do_remove(_b):
            if not confirm.get_active(): return
            async_call(lambda:self.runtime.remove_profile(name.get_text()),
                       "Profil supprimé",reload_profiles=True)
            confirm.set_active(False)
        remove.connect("clicked",do_remove)

        _run_once_on_first_map(scroll, lambda: async_call(self.runtime.profiles))
        return scroll

    def deactivate(self):
        for handle in tuple(self._task_handles):
            try: handle.cancel()
            except Exception: pass
        self._task_handles.clear()
        self.tasks=None
        self.runtime=None
        self.context=None


PLUGIN=AdeptProfilesPagePlugin()
