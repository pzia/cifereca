# P0 #5069 B11 — preuve de propriété des générations Core

L'inspection de rétention Core ne déduit plus `owned_by_updater` de la simple présence de `.cifereca-install.json`.

Pour chaque release, elle ouvre le répertoire des releases, puis la génération et son reçu par descripteurs ancrés, avec `O_NOFOLLOW`. Seul un fichier régulier non hardlinké et borné à 64 KiB est admissible ; son JSON doit être un objet avec un champ `version` non vide. `installed_at` doit être une date ISO avec fuseau si présent ; les anciennes installations sans ce champ conservent le repli historique sur le `mtime` du répertoire ouvert.

Un reçu absent, corrompu, lié symboliquement, hardlinké, trop gros ou incohérent entraîne `release_ownership_unqualified`, une génération conservée par le plan (raison `unowned`), et aucune autorisation de suppression. Le diagnostic n'ouvre jamais le contenu des releases, l'état utilisateur ou les plugins.

**Limite :** un reçu cohérent constitue seulement une preuve nécessaire, non une attestation cryptographique d'ownership ou de l'intégrité complète des références inter-magasins. `safe_to_apply=false` et `apply_supported=false` restent obligatoires jusqu'à livraison de la maintenance supervisée sous verrou avec revalidation et audit/reprise. Ne pas utiliser l'inventaire comme outil de purge.
