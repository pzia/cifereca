# P0 #5069 B2 — Inventaire de rétention en lecture seule (tranche partielle)

L'API `inspect_core_transaction_history(root, now=...)` inventorie sans suppression les **générations Core**, **journaux de transactions** et **snapshots transactionnels sous transactions/**. Elle applique le planificateur pur B1 et fournit les compteurs, références et motifs fermés.

**Sécurité :**
- Dossiers de releases, transactions et snapshots symlinkés : refus conservateur.
- Entrées de transaction illisibles, références Core absentes, transaction incohérente : signalement, aucune mutation.
- Identifiants publics hachés, sans chemins de fichiers ni contenu de journal ; pas de lecture des fichiers de livres ou d'appel plugin.
- Pointeurs `current`/`previous` et journaux incomplets sont épinglés.
- La présence d'une ancienne génération sans preuve de propriété est **conservée**.
- `safe_to_apply=false` sans exception. Aucun GC physique ni API de suppression.

**Couverture encore manquante et donc bloquante pour une vraie maintenance universelle :**
`plugin_versions` par package, `updater_backups` (distincts des sauvegardes manuelles), `persistent_staging`, consommation de snapshots, inventaire de références de rollback entre magasins, verrou partagé avec activation des plugins et test crash/TOCTOU.

Ce lot est **empilé sur B1** et ne doit pas être considéré comme la livraison complète de #5069 B. L'interface de maintenance officielle n'est pas activée. L'état P0 reste ouvert jusqu'à toutes les étapes et recette terrain.