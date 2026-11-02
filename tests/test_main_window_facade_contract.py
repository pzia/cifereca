"""GUI declaration contracts can be checked and resolve reexports without GTK."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'src/cifereca/gui/main_window.py'
CONTRACTS = json.loads((Path(__file__).parent / 'fixtures/main_window_facade.json').read_text())


def _declaration(path, name, visited=()):
    assert path not in visited, 'GUI reexport cycle'
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
        if isinstance(node, ast.ImportFrom) and node.level:
            for alias in node.names:
                if (alias.asname or alias.name) == name:
                    base = path.parent
                    for _ in range(node.level - 1):
                        base = base.parent
                    target = base.joinpath(*(node.module or '').split('.')).with_suffix('.py')
                    return _declaration(target, alias.name, (*visited, path))
    raise AssertionError(f'GUI declaration missing: {name}')


@pytest.mark.parametrize('name,expected', CONTRACTS.items())
def test_gui_facade_signature_and_descriptor_survive_extraction(name, expected):
    owner, _, method = name.partition('.')
    node = _declaration(MODULE, owner)
    if method:
        node = next(n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == method)
    actual = {'args': ast.dump(node.args, include_attributes=False),
              'returns': ast.dump(node.returns, include_attributes=False) if node.returns else None,
              'decorators': [ast.unparse(d) for d in node.decorator_list],
              'async': isinstance(node, ast.AsyncFunctionDef)}
    assert actual == expected



def test_retired_bookings_are_absent_from_facade_contract_and_runtime_ast():
    """#4186: a removed GTK renderer must not be pinned by an old fixture.

    Kept in the facade test itself: a stale key otherwise produces only an
    opaque StopIteration in the parametrized signature check and breaks P0 CI.
    """
    retired = (
        "DetailPanel.show_booking",
        "MainWindow.refresh_bookings",
        "MainWindow._build_bookings_page",
        "MainWindow._populate_bookings",
        "MainWindow._booking_selected",
    )
    for qualified in retired:
        assert qualified not in CONTRACTS, f"retired booking facade persisted: {qualified}"
        owner, _, method = qualified.partition(".")
        declaration = _declaration(MODULE, owner)
        assert not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == method
            for node in declaration.body
        ), f"retired booking facade reintroduced: {qualified}"
    assert not (MODULE.parent / "booking_view.py").exists()


def test_legacy_booking_data_model_stays_deserializable_without_gtk():
    """Retiring the reservation UI must not break v1 serialized data."""
    from dataclasses import asdict
    from cifereca.models import Booking

    historic = Booking(
        id="fixture-booking", provider="SYRACUSE",
        medium="physical", title="Historic reservation",
    )
    assert Booking(**asdict(historic)) == historic

def test_gui_namespace_core_and_cli_remain_lazy_without_pygobject():
    code = '''
import importlib.abc, sys
class NoGtk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'gi' or fullname.startswith('gi.'):
            raise AssertionError('headless imports must not require PyGObject')
sys.meta_path.insert(0, NoGtk())
import cifereca.gui
from cifereca.cli import main
from cifereca.services import LibraryService
assert cifereca.gui.__all__ == ['MainWindow']
assert 'cifereca.gui.main_window' not in sys.modules
assert 'gi' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-c', code], env=os.environ.copy(),
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
