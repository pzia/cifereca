"""#773: generated ebook/PDF fixtures exercise the four software profiles.

No device connection or hardware conformance claim. All paths are synthetic.
"""
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

import pymupdf
import pytest

from cifereca_epub_optimizer.builtin_profiles import BUILTIN_PROFILE_PLUGINS, builtin_profile
from cifereca_epub_optimizer.engine import optimize_epub
from cifereca_epub_optimizer.pdf_engine import optimize_pdf



@pytest.fixture
def documents(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[4] / "simulation"))
    from plugin_scenarios import create
    root = tmp_path / "dataset"
    return root, create(root)


@pytest.mark.parametrize("profile_id,width,height,levels", [
    ("xteink-x3", 528, 792, (0, 85, 170, 255)),
    ("xteink-x4", 480, 800, (0, 85, 170, 255)),
    ("kobo-clara-bw", 1072, 1448, None),
    ("generic-eink", 1200, 1600, None),
])
def test_generated_documents_under_exact_software_device_profile(
    documents, tmp_path, profile_id, width, height, levels,
):
    root, manifest = documents
    plugin, = [p for p in BUILTIN_PROFILE_PLUGINS if p.profile.id == profile_id]
    exported = plugin.describe()
    assert (exported["id"], exported["width"], exported["height"]) == (profile_id, width, height)
    assert exported["gray_levels"] == levels and exported["grayscale"] is True
    assert exported["max_zoom_factor"] is None
    profile = builtin_profile(profile_id)

    for name in ("optimizer-valid.epub", "publication-text.pdf", "publication-scan.pdf"):
        source = root / name
        original = source.read_bytes()
        target = tmp_path / (profile_id + "-" + name)
        if name.endswith(".epub"):
            report = optimize_epub(source, target, profile=profile)
            assert report.images_seen >= 1
            with ZipFile(source) as before, ZipFile(target) as after:
                assert after.testzip() is None and set(after.namelist()) == set(before.namelist())
                for member in before.namelist():
                    if member.endswith((".opf", ".xml", ".xhtml")):
                        assert before.read(member) == after.read(member)
        else:
            report = optimize_pdf(source, target, profile=profile)
            with pymupdf.open(source) as before, pymupdf.open(target) as after:
                assert before.page_count == after.page_count == report.pages
                for old, new in zip(before, after):
                    assert tuple(old.rect) == tuple(new.rect)
                    assert old.get_text("text") == new.get_text("text")
                if name == "publication-scan.pdf":
                    assert after[0].get_images()
                    assert not after[0].get_text("text").strip()
        assert report.source_sha256 == manifest["files"][name]
        assert report.output_sha256 == sha256(target.read_bytes()).hexdigest()
        assert source.read_bytes() == original
