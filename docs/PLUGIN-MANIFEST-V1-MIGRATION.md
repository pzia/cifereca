# Plugin manifests: canonical publication and v1 migration (#3200)

Official distributions use `requires_brokers` for named broker IDs and `requires_contracts` for category/contract/version-based service dependencies. A legacy `requires_services` entry is still interpreted as a **broker ID**, not automatically converted to a service contract. When both broker fields are present, v1 continues to require their deduplicated union. A missing broker remains a blocked activation, not an optional dependency.

| Legacy v1 syntax | Canonical publication |
|---|---|
| `requires_services: ["catalog"]` | `requires_brokers: ["catalog"]` |
| `system_requires: [{"type":"python","name":"Pillow"}]` | `system_requires: [{"type":"python-package","name":"Pillow"}]` |
| Free-form capability names | Explicit domain.action names using the documented capability grammar |

`type: python` without a name remains the interpreter-version constraint. Version constraints, import names and dependency modes retain their meaning. A free-form capability must be migrated by its author with consumer review; it must never be renamed by guessing.

The v1 installed-manifest parser remains compatible; `PluginPackageManifest.migration_warnings` reports field coordinates, and `load_manifest` logs an advisory without emitting arbitrary capability strings or installation paths. Official ZIP builds reject legacy broker fields and distribution aliases; canonical capability syntax was already required by packaging. The source conformance test enumerates the actual official directories, not a fixed list. Static inspection of the 23 indexed source manifests at de60b051 found no legacy fields, distribution aliases or free-form capability names. No source manifest rewrite was needed. Installed old/external ZIP parsing remains supported and this is not a security boundary.

A future Plugin API 2 removal requires explicit major-version authorization, a published compatibility and migration window, consumer/installed-package inventory and tests for previous installations, missing/incompatible broker and contract dependencies, update and rollback. Do not remove `Account`, `Loan` or `ProviderSession` re-exports used by existing v1 clients merely to shrink the API. No API-major change or release is authorized by #3200.
