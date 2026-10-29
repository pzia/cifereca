# Core P0 #3196 — un replay passif Kobo n'altère pas un reçu terminé

## Risque

Le miroir historique `Storage._mirror_backup_row` protégeait déjà `status`, `artifact_id` et les champs `error_message` / `attention_code` d'une acquisition `complete` contre une ancienne observation `pending` ou `discovered`.

Ce garde était incomplet : un `backup_put` passif pouvait encore remplacer `strategy` et `executor`, augmenter artificiellement `attempt_count`, remplacer `last_attempt_at` et `updated_at`, voire attribuer un `identity_id` manquant. Une simple lecture de manifeste pouvait ainsi paraître constituer une nouvelle tentative ou une réception plus récente, sans GET ni reçu autonome.

## Nouvelle frontière

Dans l'UPSERT SQLite existant (transaction de `backup_put` inchangée), lorsque le reçu courant est `complete` **et** que le nouveau statut générique est `pending` ou `discovered`, tous ces champs conservent leur valeur canonique précédente, comme déjà `status`, `artifact_id`, `error_message` et `attention_code`. Les métadonnées du reçu restent inchangées dans tous les cas comme auparavant.

Une vraie nouvelle tentative `acquiring`, une attention `attention_required`, une erreur active ou une nouvelle livraison `complete` conservent les transitions préexistantes du flux Kobo legacy, jusqu'à sa migration complète vers #3171. Le miroir `backup_items` continue d'évoluer pour la compatibilité mais ne rajeunit plus un reçu terminé sur simple replay passif.

Cette tranche n'atteste pas les octets, ne change aucun droit, ne crée aucun OriginLink et ne supprime aucune ligne ou fichier.

## Réception

`tests/test_core_p1_3196_backup_owner_upsert.py` simule de vrais reçus SQLite `complete` avec stratégie, exécuteur, tentative et horodatages distincts de la projection, puis répète les replays `pending`, `queued` et `unknown`. L'égalité **complète** du reçu est contrôlée à chaque passage ; la projection reste consultable. Un test `acquiring` valide que les mises à jour métier légitimes de la tentative suivante passent toujours.

Migration 0.7/0.8, suppression du writer `backup_put`, lecture des snapshots fournisseurs et recette terrain : #3196 reste ouvert.
