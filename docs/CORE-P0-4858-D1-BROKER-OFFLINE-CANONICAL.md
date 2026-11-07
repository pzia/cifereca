# P0 #4858 / D1 — aperçu Kobo dans la page Synchronisation

## Relais du cutover CLI au broker GUI

Avant cette tranche, `SynchronizationBrokerImpl.preview_local_materialization`
appelle `service.kobo_backup_preview(refresh_manifest=False)` sans préciser
le mode de source. Pour une session Kobo d'ABI partielle, la méthode publique
retombe alors sur le lecteur 0.7 `backup_items` : les catégories
`candidate_unverified`, `already_local_linked` ou les décomptes de
réconciliation proviennent de reçus historiques sans `OriginLink` fournisseur.

La page Synchronisation reste explicitement offline, sans consentement ni GET,
mais ce diagnostic ne doit plus être présenté comme une présence/acquisition
actuelle.

## Contrat

- Le broker force `offline_canonical_only=True` pour la source Kobo. Ce mode
  inspecte en lecture seule la liaison SQL exacte (identity/credential/borrower,
  connexion activée, y compris `account_id=""`) et la publication B existante.
- Les gardes du broker exigent `canonical_source=True`,
  `historical_canonical_only=True`, aucune entrée de manifeste courant et
  **tous les compteurs de décisions d'acquisition à zéro**. L'absence de
  liaison ou une sortie non conforme répond `preview_unavailable`.
- Seules les métriques bornées `recorded_rights`,
  `physically_verified` et `inventory_truncated` du bilan B sont projetées
  à la GUI ; chemins, SHA, borrower, offre, token et corps fournisseur restent
  inaccessibles. Les chiffres n'attestent **aucun droit encore actif**.
- L'affichage indique des **références B historiques**, séparément des copies
  physiquement vérifiées ; il ne prétend plus que le 0.7 décrit les livres
  connus ou manquants. Un aperçu tronqué reste marqué partiel.
- Aucun provider n'est connecté, aucun cache n'est rafraîchi, aucun fichier
  n'est modifié et aucun droit/GET n'est autorisé.
- La branche privée `_item_reasons` utilisée par l'exécution W2 partielle
  demeure inchangée, à migrer séparément. Les autres fournisseurs (Numilog)
  gardent leur contrat.

## Réception et limites

`tests/test_core_1346_source_preflight.py` vérifie la sélection exacte du
mode B offline, l'absence de compteurs 0.7 positifs, la validation stricte
des sorties et la présentation sans fuite privée. Les recettes SQLite du mode
Core B offline restent couvertes par
`tests/test_core_p0_4858_d1_canonical_offline_preview.py`.

**Ce n'est pas la fermeture W1/W2/D1–D4** : les écritures legacy pour les
sessions partielles et leurs lecteurs internes subsistent tant que la
convergence A→B n'est pas complète.
