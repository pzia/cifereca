"""Private BnF review contract; all images and reports are synthetic."""
import csv
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

E2E = Path(__file__).resolve().parents[1] / "e2e"
sys.path.insert(0, str(E2E))
try:
    import bnf_private_review as review
finally:
    sys.path.remove(str(E2E))

HEAD = "a" * 40


def populate(folder, theme, width):
    folder.mkdir(parents=True, exist_ok=True)
    size = (width, 1000 if width == 1600 else 900)
    image = Image.new("RGB", size, (255, 255, 255))
    image.putpixel((0, 0), (1, 2, 3))
    snapshots = []
    for stage in review.STAGES:
        png = folder / (stage + ".png")
        image.save(png)
        snapshots.append(dict(stage=stage, file=png.name, appearance=theme,
                              sha256=review.digest(png.read_bytes())))
    report = dict(status="PASS", git_sha=HEAD, scenario=f"bnf-covers-{width}",
                  backend="GTK4/X11, BnF HTML parser + provider adapter, HTTPX MockTransport",
                  width=width, height=size[1], appearance=theme, snapshots=snapshots,
                  checks=dict.fromkeys(review.CHECKS, True))
    for phase in ("cold", "offline"):
        report["notice_c_borrower_views_" + phase] = dict(
            views=4, sqlite_reads=1, image_gets_total=1, gtk_applied=4)
    (folder / "screen.json").write_text(json.dumps(report))
    return report


@pytest.fixture
def private(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    root = tmp_path / "captures"
    for theme, width in review.MATRIX:
        populate(review.capture_folder(root, theme, width), theme, width)
    return root, tmp_path / "review"


def test_complete_private_review_requires_independent_verdict_and_rechecks_bytes(private):
    root, out = private
    assert review.prepare(root, out, HEAD)["capture_count"] == 96
    with pytest.raises(review.CaptureError):
        review.verify(root, out, HEAD)
    path = out / "review.csv"
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        row.update(verdict="OK", notes="PRIVATE_NOTE")
    with path.open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=review.FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    result = review.verify(root, out, HEAD)
    assert result["capture_count"] == 96
    assert "PRIVATE_NOTE" not in repr(result)
    png = review.capture_folder(root, "light", 1366) / (review.STAGES[0] + ".png")
    png.write_bytes(png.read_bytes() + b"changed")
    with pytest.raises(review.CaptureError):
        review.verify(root, out, HEAD)


@pytest.mark.parametrize("change", ["head", "theme", "duplicate", "check", "missing",
                                    "path", "boolean_count"])
def test_refuses_incomplete_or_untrusted_native_report(tmp_path, change):
    folder = tmp_path / "fixture"
    data = populate(folder, "light", 1366)
    if change == "head":
        data["git_sha"] = "b" * 40
    elif change == "theme":
        data["snapshots"][0]["appearance"] = "dark"
    elif change == "duplicate":
        data["snapshots"][-1] = data["snapshots"][0]
    elif change == "check":
        data["checks"][review.CHECKS[0]] = 1
    elif change == "missing":
        data["snapshots"].pop()
    elif change == "path":
        data["snapshots"][0]["file"] = "../private.png"
    else:
        data["notice_c_borrower_views_cold"]["sqlite_reads"] = True
    (folder / "screen.json").write_text(json.dumps(data))
    with pytest.raises(review.CaptureError):
        review.validate_folder(folder, HEAD, "light", 1366)


def test_refuses_symlinks_and_hardlinks(tmp_path):
    original = tmp_path / "original"
    original.write_bytes(b"private")
    alias = tmp_path / "alias"
    alias.symlink_to(original)
    with pytest.raises(OSError):
        review.read_regular(alias, 100)
    alias.unlink()
    alias.hardlink_to(original)
    with pytest.raises(review.CaptureError):
        review.read_regular(alias, 100)


def test_local_orchestrator_uses_four_private_bnf_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(review, "clean_head", lambda: HEAD)
    calls = []

    def fake_run(args, **kwargs):
        width = int(args[args.index("--bnf-width") + 1])
        theme = args[args.index("--appearance") + 1]
        folder = Path(args[args.index("--out") + 1]) / "bnf-covers-1366"
        populate(folder, theme, width)
        calls.append((theme, width, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(review.subprocess, "run", fake_run)
    root = tmp_path / "captures"
    result = review.capture(root, python="python", sha=HEAD)
    assert result == dict(status="BNF_CAPTURES_READY_FOR_PRIVATE_REVIEW",
                          git_sha=HEAD, scenario_count=4)
    assert [(theme, width) for theme, width, _ in calls] == list(review.MATRIX)
    assert all(kw["stdout"] == kw["stderr"] == review.subprocess.DEVNULL
               for _, _, kw in calls)
    with pytest.raises(OSError):
        review.capture(root, python="python", sha=HEAD)
    monkeypatch.setenv("CI", "true")
    with pytest.raises(review.CaptureError):
        review.capture(tmp_path / "no-ci", python="python", sha=HEAD)
