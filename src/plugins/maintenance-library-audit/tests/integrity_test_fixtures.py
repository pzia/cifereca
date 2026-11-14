"""Test-only stable fixtures, independent of collected test modules (#5376)."""
from __future__ import annotations

class Control:
    def __init__(self):
        self.events = []

    def cancelled(self):
        return False

    def check_cancelled(self):
        return None

    def report(self, **event):
        self.events.append(dict(event))


