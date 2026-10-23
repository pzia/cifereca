"""Collect BnF worker assertions in the existing PR Core gate."""
import importlib.util
from pathlib import Path

_path = (Path(__file__).resolve().parents[1] / 'src/plugins/provider-bnf-pret'
         / 'tests/test_reader_worker.py')
_spec = importlib.util.spec_from_file_location('bnf_reader_worker_contract_tests', _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
ReaderWorkerTests = _module.ReaderWorkerTests
