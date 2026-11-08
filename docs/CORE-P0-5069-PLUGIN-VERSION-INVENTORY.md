# P0 #5069 B3 — Versions historiques de plugins (lecture seule)

Le plugin installé utilise `data/plugins/packages/{package_id}/{version}`, avec un pointeur `current.json` (`active`, `previous`) par package. `inspect_plugin_version_history(package_root, now=...)` inventorie les répertoires de versions sans activation, lecture de payload ni mutation.

- L'unité de rétention est le **package**, non le magasin global ; 24 h glissantes et minimum deux versions **par package**, en conservant aussi les versions `active` et `previous`.
- Les reçus `.cifereca-install.json` et les manifests doivent correspondre exactement au package/version pour établir une provenance basique ; à défaut, les entrées restent conservées et `plugin_version_ownership_unqualified` est exposé.
- Les symlinks de magasin, package, version, pointeur ou reçus ne sont pas suivis ; le pointeur malformé épingle **toutes** les versions et bloque l'application.
- Les identifiants de packages/versions sont remplacés par SHA-256 dans l'inventaire public. Aucun chemin ni metadata privé n'est renvoyé.
- **`safe_to_apply=False` sans exception.** L'ownership complet, les références entre transactions, backups/restores et processus de plugins actifs n'étant pas démontrés, ce lot n'autorise aucune suppression et n'installe aucun GC.

Suite requise pour #5069 : liaison au graphe cross-store, qualification des backups updater et staging, verrouillage commun aux activations plugin, maintenance inspect/plan/apply avec TOCTOU et audit/reprise crash-safe.