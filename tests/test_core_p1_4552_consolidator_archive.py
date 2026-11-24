from hashlib import sha256
from io import BytesIO
import json
import zipfile

import pytest

from cifereca.core.notice_consolidator_archive import qualify_consolidator_archive
from tests.test_core_p1_4552_consolidator_manifest import MEMBER, SOURCE, row


def payload(*, extra=(), manifest=None, source=SOURCE):
    raw = dict(manifest=1, package=dict(id="package.age", version="1.0.0"),
               plugins=[dict(id="plugin.age.alpha", type="meta.bundle",
                             version="1.0.0", capabilities=["metadata.consolidator"])],
               notice_text_consolidators=[row()])
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("cifereca-plugin.json", json.dumps(raw) if manifest is None else manifest)
        archive.writestr(MEMBER, source)
        for name, data in extra:
            archive.writestr(name, data)
    return out.getvalue()


def qualify(data):
    return qualify_consolidator_archive(data, expected_sha256=sha256(data).hexdigest())


def test_digest_pinned_archive_is_inert():
    data = payload()
    result = qualify(data)
    assert result.archive_sha256 == sha256(data).hexdigest()
    assert result.package_id == "package.age"
    assert result.package_version == "1.0.0"
    candidate, = result.candidates
    assert candidate.installed.source.encode() == SOURCE
    with pytest.raises(ValueError, match="digest_mismatch"):
        qualify_consolidator_archive(data, expected_sha256="0"*64)


@pytest.mark.parametrize("extra", [
    (("consolidators/../age.py", SOURCE),),
    (("consolidators//age.py", SOURCE),),
    (("consolidators/./age.py", SOURCE),),
    (("consolidators\\\\age.py", SOURCE),),
])
def test_archive_rejects_extraction_aliases(extra):
    with pytest.raises(ValueError, match="archive_invalid"):
        qualify(payload(extra=extra))


def test_duplicate_manifest_and_member_digest_refused():
    with pytest.warns(UserWarning, match="Duplicate name"):
        data = payload(extra=(("cifereca-plugin.json", "{}"),))
    with pytest.raises(ValueError, match="archive_invalid"):
        qualify(data)
    with pytest.raises(ValueError, match="archive_invalid"):
        qualify(payload(manifest='{"manifest":1,"manifest":1}'))
    with pytest.raises(ValueError, match="archive_invalid"):
        qualify(payload(source=SOURCE+b"changed"))
