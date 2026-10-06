"""Keyboard intent independent of GTK callbacks and automatic UI updates."""
class SearchEnterSequence:
    def __init__(self):
        self.armed = None

    def reset(self):
        self.armed = None

    def press(self, state):
        if len(state.text) < 2:
            self.reset()
            return False
        online = self.armed == state
        self.armed = None if online else state
        return online
