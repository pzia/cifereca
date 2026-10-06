from __future__ import annotations


class PluginRuntimeError(RuntimeError):
    pass


class ManifestError(PluginRuntimeError):
    pass


class PackageInstallError(PluginRuntimeError):
    pass


class DependencyError(PluginRuntimeError):
    pass


class PluginActivationError(PluginRuntimeError):
    pass
