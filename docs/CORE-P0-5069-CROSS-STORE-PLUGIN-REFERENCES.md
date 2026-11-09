# P0 #5069 B8 — Références transactions Core → versions plugins

La commande officielle `cifereca-update --retention-inspect --json`
expose `cross_store_reference_inspection` : un comptage agrégé et sans
nom utilisateur des versions de plugins citées par les états
`before` / `after` de chaque reçu transactionnel Core.

Le lecteur ouvre uniquement les `transaction.json` existants via le
lecteur `NOFOLLOW` borné de B2, sans parcourir les copies de restauration
ni lire les exécutables plugins. Les coordonnées package/version servent
uniquement au calcul d'identifiants opaques `sha256` conformes à
l'inventaire des versions installées. Les références à un plugin absent,
une version non attestée, un reçu malformé ou un chemin/symlink
inattendu restent **non résolues** et bloquent toute prétention de sûreté.

Un ID opaque trouvé dans l'inventaire ne suffit pas : la version doit
avoir passé la qualification d'ownership du magasin plugin. Les rapports
ne contiennent ni ID package, ni version, ni chemin, ni donnée sauvegardée.

Ce contrôle n'est **pas le graphe complet** : les dépendances
transactions ↔ snapshots ↔ générations Core ↔ sauvegardes et
staging actif demeurent hors qualification. Le champ
`pending_stores.cross_store_reference_proofs` est maintenu,
et `safe_to_apply` reste `false`. Aucune purge, aucun changement
de mécanisme de rollback et aucune traversée de bibliothèque personnelle.
