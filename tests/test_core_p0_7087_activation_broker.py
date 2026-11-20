"""Activation lifecycle of optional scoped read brokers, without GTK."""
import json
from pathlib import Path
from types import SimpleNamespace as NS
import zipfile

from cifereca.core.activation_scoped_broker import ActivationBrokerResolver, ActivationScopedReadFactory
from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from tests.test_plugin_lifecycle_regression import _archive


def test_read_lease_drops_result_when_activation_is_revoked_mid_call():
    holder = []
    calls = []
    def work():
        calls.append(True)
        holder[0].close()
        return "private evidence"
    factory = ActivationScopedReadFactory(lambda: NS(read=work, writer=lambda: 1), ("read",))
    resolver = ActivationBrokerResolver(lambda key: None, lambda key: factory,
                                        "fixture", ("read-port",))
    holder.append(resolver)
    assert resolver("undeclared") is None
    lease = resolver("read-port")
    assert resolver("read-port") is lease
    assert not hasattr(lease, "writer") and not hasattr(lease, "revoke")
    assert lease.read() is None and calls == [True]
    assert lease.read() is None and calls == [True]
    assert resolver("read-port") is None


def _package(tmp_path, *, failure=False, declared=True):
    archive = tmp_path / "scoped.zip"
    manifest = {
        "manifest": 1,
        "package": {"id": "org.test.scoped", "version": "1.0.0",
                    "name": "fixture", "publisher": "tests", "plugin_api": ">=1,<2"},
        "plugins": [{"id": "test.scoped", "type": "maintenance.tool",
                     "entrypoint": "scoped_fixture:PLUGIN",
                     "requires_brokers": ["fixture.read"] if declared else []}],
    }
    source = """
from cifereca.plugin_api import MaintenanceToolInfo, MaintenancePlan, MaintenanceResult
class Plugin:
    def activate(self, context):
        self.context = context
        self.lease = context.broker("fixture.read")
        self.during_activate = self.lease.read() if self.lease else None
        return self
    def describe(self): return MaintenanceToolInfo("fixture", "Fixture")
    def plan(self, context): return MaintenancePlan("fixture", "read")
    def run(self, plan, control): return MaintenanceResult(True)
    def deactivate(self):
        self.during_deactivate = self.lease.read() if self.lease else None
PLUGIN = Plugin()
"""
    if failure:
        source = source.replace("return self\n", 'raise RuntimeError("synthetic activation failure")\n')
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("cifereca-plugin.json", json.dumps(manifest))
        bundle.writestr("scoped_fixture.py", source)
    return archive


def _manager(tmp_path, factory):
    return PluginManager(
        package_store=PackageStore(tmp_path / "packages"),
        state_store=PluginStateStore(tmp_path / "plugins-state.json"),
        brokers={"fixture.read": factory},
    )


def test_real_package_disable_and_reactivation_revoke_old_context(tmp_path):
    created = []
    def reader():
        number = len(created) + 1
        created.append(number)
        return NS(read=lambda: number)
    manager = _manager(tmp_path, ActivationScopedReadFactory(reader, ("read",)))
    try:
        manager.install(_package(tmp_path))
        service = manager.service("test.scoped")
        assert service.during_activate == 1
        old, context = service.lease, service.context
        assert old.read() == 1 and context.broker("fixture.read") is old
        unrelated = _archive(tmp_path, package_id="org.test.unrelated", plugin_id="test.unrelated",
                             module_name="unrelated_scoped_fixture",
                             source="class P:\n def activate(self, ctx): return self\nPLUGIN=P()\n")
        manager.install(unrelated)
        assert manager.service("test.scoped") is service and old.read() == 1
        manager.set_enabled("test.scoped", False)
        assert manager.service("test.scoped") is None
        assert service.during_deactivate is None
        assert old.read() is None and context.broker("fixture.read") is None
        manager.set_enabled("test.scoped", True)
        new = manager.service("test.scoped")
        assert new.lease is not old and new.during_activate == 2
        assert old.read() is None
        fresh = new.lease
    finally:
        manager.close()
    assert fresh.read() is None


def test_failed_activation_revokes_reader(tmp_path):
    retained = []
    class Factory(ActivationScopedReadFactory):
        def for_activation(self, *args, **kwargs):
            lease = super().for_activation(*args, **kwargs)
            retained.append(lease)
            return lease
    manager = _manager(tmp_path, Factory(lambda: NS(read=lambda: 7), ("read",)))
    try:
        manager.install(_package(tmp_path, failure=True))
        assert manager.status("test.scoped").state == "error"
        assert manager.service("test.scoped") is None
        assert len(retained) == 1 and retained[0].read() is None
    finally:
        manager.close()


def test_undeclared_scoped_port_not_exposed_to_package(tmp_path):
    manager = _manager(tmp_path, ActivationScopedReadFactory(lambda: NS(read=lambda: 1), ("read",)))
    try:
        manager.install(_package(tmp_path, declared=False))
        assert manager.service("test.scoped").lease is None
    finally:
        manager.close()
