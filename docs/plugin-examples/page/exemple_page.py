"""Educational page: no account, filesystem or network access."""
from cifereca.plugin_api import UiPageContribution, UiActionContribution


class PagePlugin:
    def activate(self, context):
        self.context = context
        return self

    def pages(self):
        return [UiPageContribution('example-page', 'Exemple', factory=self.build,
                                  actions=(UiActionContribution('message', 'Message', lambda host: host.toast('Exemple'), 'dialog-information-symbolic'),))]

    @staticmethod
    def build(host):
        from gi.repository import Gtk
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        label = Gtk.Label(label='Exemple de plugin page'); box.append(label)
        if callable(getattr(host, 'build_action_bar', None)):
            box.append(host.build_action_bar((UiActionContribution('message', 'Message', lambda h: h.toast('Exemple'), 'dialog-information-symbolic'),)))
        return box

    def deactivate(self):
        pass


PLUGIN = PagePlugin()
