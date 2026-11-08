# #5069 B5 — Inventaire des sauvegardes produites par l'Updater

Deux magasins **explicitement** gérés par l'Updater sont qualifiés en lecture seule :

- `state_dir()/update-backups` : sauvegardes pré-activation créées par `updater._backup_persistent_state`, avec dossier `YYYYMMDDTHHMMSSZ-<SHA10>` et reçu `backup.json`. Le nom, la date et le SHA tronqué du label doivent correspondre ; toute preuve absente ou contradictoire interdit de classer le répertoire comme bien contrôlé par l'Updater.
- `state_dir()/dev-updates` : sauvegardes intermédiaires du dev updater ; **aucun reçu durable atteste actuellement chaque génération**, donc toutes sont conservées et marquées non qualifiées.

Les profils, SQLite live, livres et `manual-backups` ou autres répertoires de l'utilisateur ne sont **ni énumérés ni nettoyés**. Le contenu interne des backups et les identifiants de packages ne sont pas lus/renvoyés ; seules les métadonnées fermées de `backup.json` sont vérifiées. Les répertoires symlinkés sont refusés.

La politique `24 h + minimum 2` est calculée par le moteur B1 sur chaque unité qualifiée, mais **`safe_to_apply=false` sans exception** : une sauvegarde pourrait encore être requise par une transaction/rollback, la portée cross-store n'est pas attestée et la sérialisation est à consolider. Aucune commande de suppression n'est implémentée.