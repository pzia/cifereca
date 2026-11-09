# P0 #5069 B5 — Backups Core pré-activation

`inspect_core_preactivation_backups(state_dir() / "update-backups", now=...)` inventorie **en lecture seule** les sauvegardes automatiques de l'Updater Core créées avant activation. Elles peuvent contenir SQLite, configuration et plugins : l'outil n'ouvre **jamais leurs contenus**, ne suit pas les symlinks et n'inspecte pas les sauvegardes manuelles.

Une génération n'est qualifiée que si son nom `YYYYMMDDTHHMMSSZ-<digest10>` correspond au reçu `backup.json` (clés exactes `label`, `created_at`) émis par `_backup_persistent_state`. Le reçu est lu par un descripteur `O_NOFOLLOW`, limité à 16 Kio. Les labels, chemins et données utilisateur n'apparaissent pas dans le rapport ; les identifiants sont SHA-256 opaques.

Le plan de conservation applique l'union **24 h + 2 générations minimales**. Un reçu absent, invalide ou un staging non qualifié empêche toute conclusion destructive. Le graphe des références transactionnelles et de rollback reste incomplet : `safe_to_apply=false` **toujours**, même avec des candidats théoriques.

Ce lot ne fusionne pas les magasins `dev-updates` et `update-backups`, car leur ownership et leur producteur diffèrent. L'agrégation à `--retention-inspect` est un lot de consolidation ultérieur.
