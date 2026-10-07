class Titlebar:
    def activate(self, context):
        self.context = context
        return self

    def build(self, host):
        from gi.repository import Adw

        return Adw.HeaderBar()

    def selected_actions(self):
        return self.context.configuration().get("actions", [])


class Widgets:
    def activate(self, context):
        return self

    def describe(self, widget_id):
        return {"id": widget_id} if widget_id == "query-controls" else None

    def build_query_controls(self, providers, changed, activate, *, library=False):
        from cifereca.gui.query_controls import QueryControls

        return QueryControls(providers, changed, activate, library=library)
