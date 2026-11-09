# P0 #5069 B4 — Ownership des sauvegardes automatiques DevUpdater

Le DevUpdater écrit ses sauvegardes sous `state_dir()/dev-updates/<id>/plugins/`. Les anciennes sauvegardes ne portent pas de preuve fiable d'ownership. **Aucune sauvegarde existante ne doit être déduite supprimable à partir de son nom ou de sa date de modification.**

## Nouveau reçu d'ownership

Pour les **nouvelles** sauvegardes, avant toute mutation des plugins, le DevUpdater :

1. Crée un répertoire de génération privé en mode `0700` et copie les packages à sauvegarder.
2. Atteste la liste des répertoires sauvegardés avec un SHA-256 trié, le nombre de packages, le SHA-256 du snapshot et un horodatage timezone-aware.
3. Écrit exclusivement `.cifereca-updater-owned.json` (`O_EXCL`, `O_NOFOLLOW`, mode `0600`), `fsync` du fichier puis du répertoire.
4. Refuse l'installation si la preuve ne peut pas être écrite ; une panne avant ce point ne laisse que des sauvegardes non qualifiées, **à conserver**. Un reçu partiel après crash n'autorise jamais le GC.

Les noms privés de packages ne sont pas inscrits dans le reçu. Le reçu **atteste la création par l'Updater, pas la cohérence de chaque octet de backup ni la libération des dépendances transactionnelles**.

## Inventaire sans purge

`inspect_dev_updater_backups(backups_root, now=...)` qualifie exclusivement les sauvegardes avec reçu cohérent, modes privés, hash des noms et marqueur de génération. Les sauvegardes anciennes, les liens symboliques, les entrées inconnues, les reçus modifiés ou incomplets restent hors éligibilité.

Le moteur commun applique le plancher **2 dernières générations** et la fenêtre **24 h**. Toutefois `safe_to_apply=false` **sans exception** : le graphe de références Core/DevUpdater, la conservation des preuves de rollback et le staging persistant restent à inventorier et sérialiser avant toute purge.

Tests synthétiques : droits, refus de duplicat, corruption, symlinks, dates, fenêtre 24 h et absence de données privées dans la projection. Aucun profil ni sauvegarde réel n'est manipulé. Cette PR n'active pas `maintenance apply` et ne ferme pas #5069.

La commande officielle `cifereca-update --retention-inspect --json` agrège à présent un troisième plan `plans.updater_backups` (Core/transactions, plugins et backups DevUpdater), tout en exposant les magasins encore incomplets dans `pending_stores`. Cette projection reste strictement en lecture seule et conserve `safe_to_apply=false`.
