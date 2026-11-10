"""P0 #5054: the legacy Numilog direct GET cannot bypass live Core admission."""
import ast
from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1]
          / "src/cifereca/core/numilog_direct_acquisition.py")


def _function(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "acquire_numilog_direct")
    return next(node for node in ast.walk(owner)
                if isinstance(node, ast.FunctionDef)
                and node.name == name)


def test_direct_get_receives_current_core_admission_without_unguarded_fallback():
    text = SOURCE.read_text(encoding="utf-8")
    assert "core_admission_current=delivery_admission_current" in text
    direct_calls = [
        node for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "download_direct"
    ]
    assert len(direct_calls) == 1
    assert any(keyword.arg == "core_admission_current"
               for keyword in direct_calls[0].keywords)
    lease = _function("delivery_admission_current")
    lease_text = ast.get_source_segment(text, lease)
    assert "session=pinned_session" in lease_text
    assert "admission_scope(item) == pinned_scope" in lease_text
    assert "startup_authorized()" in lease_text
    assert "recovery_authorized()" in lease_text
    assert "return False" in lease_text
    assert "if not delivery_admission_current():" in text


def test_provider_checks_admission_during_download():
    path = (Path(__file__).resolve().parents[1]
            / "src/plugins/provider-numilog/cifereca_numilog.py")
    code = path.read_text(encoding="utf-8")
    assert "def download_direct(self,item,target_dir,*,expected_offer_id=None,expected_identity_id=None," in code
    assert "core_admission_current=None" in code
    method_start = code.index("    def download_direct(")
    method_end = code.index("\nclass NumilogPlugin", method_start)
    method = code[method_start:method_end]
    assert "require_core()" in method
    assert "core_admission_current=core_admission_current" in method
    assert "return self._download(" in method
