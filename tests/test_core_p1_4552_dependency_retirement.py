"""Q12 shared dependency repair invalidates pins, unlike normal shutdown."""
from hashlib import sha256
from types import SimpleNamespace

import pytest

from cifereca.core.notice_descriptive_selection_store import read_descriptive_selection_txn
from tests.test_core_p1_4552_age_core_composition import archive_bytes, open_core


def read_pin(core):
    with core.storage._lock, core.storage._connect() as sql:
        sql.execute("BEGIN")
        return read_descriptive_selection_txn(sql, canonical_type="age", scope="fr:editions")


@pytest.mark.parametrize("changed", [False, True])
def test_explicit_dependency_repair_revokes_only_on_mutation(tmp_path, monkeypatch, changed):
    data = archive_bytes()
    digest = sha256(data).hexdigest()
    archive = tmp_path / "age.zip"
    archive.write_bytes(data)
    with open_core(tmp_path) as core:
        core.plugins.installer.install(archive)
        core.plugins.refresh()
        pin = core.configure_notice_consolidator(
            data, expected_sha256=digest, canonical_type="age", scope="fr:editions",
            selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
            expected_generation=0,
        )
        calls = []
        deps_type = type(core.plugins.python_dependencies)
        def ensure(*args, **kwargs):
            return SimpleNamespace(changed=changed, commit=lambda: calls.append("commit"),
                                   rollback=lambda: calls.append("rollback"))
        monkeypatch.setattr(deps_type, "ensure", ensure)
        monkeypatch.setattr(deps_type, "diagnostic", lambda *args: [])
        assert core.plugins.ensure_python_dependencies(activate=False) == []
        assert calls == ["commit"]
        latest = read_pin(core)
        if changed:
            assert latest.selection is None and latest.generation > pin.generation
        else:
            assert latest == pin
    with open_core(tmp_path) as core:
        assert read_pin(core) == latest
