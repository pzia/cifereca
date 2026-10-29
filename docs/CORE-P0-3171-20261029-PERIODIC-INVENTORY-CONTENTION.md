# Core P0 #3171 — collision Sync du 29 octobre 2026

## Corpus et portée

Uniquement les événements horodatés **2026-10-29 en Europe/Paris** du
`cifereca-journal-brut4.zip` : 434 événements entre 06:59:23 et 07:02:44.
Les occurrences des 19–28 octobre ne font pas partie de cette réception.
Les références de comptes/prêts et les chemins utilisateur ne sont pas copiés ici.

À 07:00:05, `opening-sync` (task 2) démarre. Son travail dure **86,372 s**
et se termine à 07:01:32. À 07:01:06, le minuteur d'inventaire périodique
déclenche un `source-sync` (task 3), alors que le verrou de la Sync
d'ouverture est encore détenu. Les deux opérations Numilog et Kobo reçoivent
`operation_busy` (2 ms, `lock_acquire_ms=0`) ; la tâche automatique échoue.
Ce refus **protège correctement** la transaction Core : le défaut est le
démarrage concurrent du travail périodique, pas le verrou.

## Correction

- `TaskRunner.is_active_automatic_opening()` observe seulement sa réservation
  Core d'ouverture automatique, sous verrou interne, jusqu'à la livraison GTK.
- Le minuteur périodique diffère le tick tant que cette ouverture reste active.
  Le prochain heartbeat recalcule droits, sessions et consentement **dans le
  broker existant** ; aucun horodatage de succès ni droit n'est fabriqué.
- Le worker recontrôle cette condition avant le broker si l'ouverture débute
  après l'enqueue. En cas d'autre compétition, le verrou Core et les refus
  habituels restent intacts.
- Aucun fournisseur, aucune préférence de consentement, SQLite, session,
  publication Artifact/Placement/OriginLink ou traitement ADEPT modifiés.

## Régressions déterministes

`tests/test_core_p0_1346_source_inventory_timer.py` : tick pendant ouverture,
reprise à l'heartbeat suivant, course worker/enqueue et probe inaccessible.

`tests/test_core_p0_981_task_coalescing.py` : vraie réservation TaskRunner
entre soumission, exécution et remise GTK finale.

Ces tests vérifient le **défaut précis observé le 29 octobre**, pas la réussite
réelle d'une synchronisation fournisseur. La recette Core complète et
l'installation/rollback doivent passer sur le dernier SHA de la PR.

## Hors champ / anomalies encore ouvertes

- 2 `cross_identity_loan` : refus d'admission, statut de conflit effectif
  indéterminable sans analyse des portées SQLite exactes ; aucune permission
  d'assouplir la garde.
- 1 `persisted_adept_attention` : refus conservatoire d'une nouvelle
  consommation ACSM ; intervention explicite nécessaire.
- 3 `rights snapshot read failed` : 2 Kobo (propriétaire non attesté) et
  1 Paris (propriétaire non vérifié), relevant aussi des contrats Plugins #3252.
- 3 achats Numilog `needs_reconciliation`, 3 vérifiés présents : 0 tentative,
  `preflight_failed` ; poursuivre #3171 sans téléchargement automatique
  en l'absence de preuve d'autorisation exacte.
- 1 scan local invalide sur 643 fichiers ; aucune mutation/destruction déduite.
- Aucun événement `source_sync_failed` ou `snapshot_changed` ce jour-là.
- #3642 : aucune trace d'exécution du CLI recette dans cette courte fenêtre ;
  la réception locale `create → verify → run → destroy` reste distincte.
