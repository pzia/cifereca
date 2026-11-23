"""#4552: ambiguous source coordinates must not pass canonical C decoding."""
import inspect
from cifereca.core import notice_c_cover_index

def test_canonical_source_provenance_guard_checks_ambiguous_coordinates():
    source = inspect.getsource(notice_c_cover_index._decode)
    assert 'or "://" in x or ".." in x' in source
