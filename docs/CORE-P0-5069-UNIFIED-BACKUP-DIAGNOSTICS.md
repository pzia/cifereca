# P0 #5069 B6 — Diagnostic commun des historiques de backups

La commande officielle `cifereca-update --retention-inspect --json` projette désormais **deux plans distincts** dans `plans` :

- `updater_backups` : sauvegardes créées par le DevUpdater dans `state/dev-updates`, qualifiées par reçu privé.
- `core_preactivation_backups` : sauvegardes de l'Updater Core dans `state/update-backups`, qualifiées par reçu producteur `backup.json`.

Le diagnostic n'ouvre jamais les fichiers `library.sqlite3`, `config/` ni `plugins/` de ces backups. Il n'invente aucune référence de rollback entre les deux magasins, ne cumule pas les candidats et ne modifie aucun fichier. Les chemins sont résolus sans création XDG (`state_dir(create=False)`, `data_dir(create=False)`).

Le résultat conserve **`safe_to_apply=false` sans exception**. Les blockers `cross_store_reference_proofs`, `core_backup_dependencies_unqualified`, `cross_store_backup_dependencies_unqualified` et `persistent_staging` restent ouverts. Il ne s'agit ni d'une API `apply` ni d'un GC.
