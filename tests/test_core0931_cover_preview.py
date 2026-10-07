from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / "src/cifereca/gui/cover_loader.py"
PREVIEW = ROOT / "src/cifereca/gui/cover_preview.py"
BOOK = ROOT / "src/cifereca/gui/book_widgets.py"


def _module_source(path: Path) -> str:
    return ast.unparse(ast.parse(path.read_text()))


def _method_source(path: Path, class_name: str, method_name: str) -> str:
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def test_preview_generation_uses_dedicated_two_worker_pool():
    source = _method_source(LOADER, "CoverLoader", "__init__")
    assert "ThreadPoolExecutor(max_workers=2, thread_name_prefix='cifereca-preview')" in source


def test_list_covers_request_at_least_three_x_logical_dimensions():
    source = _module_source(BOOK)
    assert "scale = max(3, int(self.get_scale_factor()))" in source
    assert "width=self._width * scale" in source
    assert "height=self._height * scale" in source


def test_preview_cache_key_depends_on_source_fingerprint_and_requested_size():
    source = _module_source(PREVIEW)
    assert "info.st_mtime_ns" in source
    assert "info.st_size" in source
    assert "width" in source and "height" in source
    assert "hashlib.sha256(key.encode()).hexdigest()" in source


def test_preview_never_enlarges_original_and_preserves_ratio():
    source = _module_source(PREVIEW)
    assert "min(width, info[1])" in source
    assert "min(height, info[2])" in source
    assert "True" in source  # preserve_aspect_ratio argument


def test_preview_work_is_bounded_and_deduplicated_by_key():
    source = _method_source(LOADER, "CoverLoader", "load_preview")
    assert "key = (str(source), width, height, force)" in source
    assert "future = self._preview_jobs.get(key)" in source
    assert "if len(self._preview_jobs) >= 64:" in source
    assert "self._preview_jobs[key] = future" in source


def test_preview_limits_and_atomic_png_write_are_preserved():
    source = _module_source(PREVIEW)
    assert "MAX_PIXELS = 50000000" in source
    assert "MAX_BYTES = 24 * 1024 * 1024" in source
    assert "not 1 <= width <= 2048" in source
    assert "not 1 <= height <= 2048" in source
    assert "pixbuf.savev(str(temp), 'png', [], [])" in source
    assert "temp.replace(dest)" in source


def test_preview_failure_falls_back_to_original_and_is_counted():
    source = _method_source(LOADER, "CoverLoader", "load_preview")
    assert "self._stats['preview_fallback'] += 1" in source
    assert "return source" in source


def test_original_path_is_kept_for_non_list_detail_sizes():
    source = _module_source(BOOK)
    assert "if self._width <= 80 and hasattr(loader, 'load_preview')" in source
    assert "else:" in source
    assert "loader.load(self._url, received" in source
