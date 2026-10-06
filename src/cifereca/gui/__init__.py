"""Couche GUI.

L'import est volontairement paresseux afin que les modules non-GTK (CLI, tests,
pipeline de couvertures) restent utilisables sur une machine sans PyGObject.
"""

__all__ = ["MainWindow"]


def __getattr__(name: str):
    if name == "MainWindow":
        from .main_window import MainWindow

        return MainWindow
    raise AttributeError(name)
