"""#6397: malformed private reader identity must not touch session authority."""
import pytest
from cifereca.core.notice_c_authenticated_web_choices import _native_reader_scope
from cifereca.core.search_orchestration import SearchReadScope


@pytest.mark.parametrize("control", [chr(0x200b), chr(0x202e), chr(0x2028), chr(0x2029), chr(0x0080)])
def test_authenticated_reader_scope_rejects_invisible_identity(control):
    service = object()
    scope = SearchReadScope(service, object(), "reader" + control + "identity", 0)
    assert _native_reader_scope(scope, service) is False
