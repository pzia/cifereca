# P0 #5069 — même preuve d'ownership pour le GC physique Core et son inventaire

La politique 24 h glissantes + minimum deux n'autorise pas à effacer un répertoire dont l'Updater ne peut prouver l'origine. `updater._gc` valide maintenant le reçu `.cifereca-install.json` de toute release candidate via le même lecteur ancré et borné que l'inventaire Core (répertoires et reçu O_NOFOLLOW, fichier régulier non hardlinké, version et date valides). Une release non qualifiée reste retenue, même très vieille.

Une installation legacy sans reçu peut continuer de fonctionner ; son timestamp de répertoire contribue à l'ordonnancement conservateur, mais la simple ancienneté ne suffit jamais à autoriser une purge. Tests offline : release ancienne avec reçu valide supprimée, autres releases anciennes aux reçus absents, invalides, symlinkés ou hardlinkés conservées ; aucun accès aux fichiers privés ciblés.

Cette protection du GC des releases ne remplace pas l'apply supervisé des autres magasins. #5069 reste ouvert tant que l'entretien des historiques transactions/plugins/backups/staging n'est pas physiquement implémenté.