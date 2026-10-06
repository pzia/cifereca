from .categories import CORE_CATEGORIES, CategoryRegistry
from .errors import DependencyError, ManifestError, PackageInstallError, PluginActivationError, PluginRuntimeError
from .manager import PluginManager
from .configuration import PluginConfigError, PluginConfigStore, validate_plugin_config
from .manifest import MANIFEST_FILENAME, PackageDefinition, PluginPackageManifest, load_manifest, parse_manifest
from .packages import InstalledPackage, PackageInstaller, PackageStore
from .state import PluginStateStore

__all__ = [
    "CORE_CATEGORIES", "CategoryRegistry", "DependencyError", "ManifestError",
    "PackageInstallError", "PluginActivationError", "PluginRuntimeError", "PluginManager", "PluginConfigError", "PluginConfigStore", "validate_plugin_config",
    "MANIFEST_FILENAME", "PackageDefinition", "PluginPackageManifest", "load_manifest",
    "parse_manifest", "InstalledPackage", "PackageInstaller", "PackageStore", "PluginStateStore",
]
