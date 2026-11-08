class Titlebar:
    def activate(self, context):
        self.context = context
        return self

    def build(self, host):
        from gi.repository import Adw

        return Adw.HeaderBar()

    def selected_actions(self):
        selection = self.context.configuration().get("actions", [])
        if type(selection) is not list:
            return selection
        # #5178: stale reservations-era titlebar preferences are inert.
        # Retire only this known obsolete ID at the read boundary, without
        # rewriting preferences or bringing back the bookings page/action.
        return [
            item for item in selection
            if not (type(item) is str and item == "bookings.open")
        ]


class Widgets:
    def activate(self, context):
        return self

    def describe(self, widget_id):
        return {"id": widget_id} if widget_id == "query-controls" else None

    def build_query_controls(self, providers, changed, activate, *, library=False):
        from cifereca.gui.query_controls import QueryControls

        return QueryControls(providers, changed, activate, library=library)
