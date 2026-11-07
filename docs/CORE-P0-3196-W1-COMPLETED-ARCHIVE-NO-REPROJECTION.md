# P0 #3196 — W1 : reçus Kobo déjà complets, pas de reprojection 0.8

## Dépendance au writer 0.7 encore active

`Storage.backup_put()` persiste les reçus de sauvegarde historique
dans `backup_items` puis projette vers les droits/acquisitions/artefacts
0.8 lorsqu'un compte est correctement identifié. Le chemin W2
`_refresh_manifest_record` réécrit les reçus existants pour enrichir
la couverture et le titre. **Une ligne Kobo déjà `complete` n'a pas
effectué un second GET ni obtenu un nouveau reçu de livraison** :
son second UPSERT ne doit jamais faire repasser un SHA/chemin historique
par `_mirror_backup_row` et le présenter comme droit ou présence B
indépendamment attestés.

## Contrat atomique

Pour Kobo uniquement, sous la même transaction `BEGIN IMMEDIATE`
qui protège la collision de propriétaire :

- sonder **avant** l'UPSERT si `backup_items.status` était déjà
  `complete` ou `ok` ;
- le checkpoint 0.7 peut toujours mettre à jour ses seules
  métadonnées historiques (titre, couverture, date, statut, etc.) ;
- si le statut demeure `complete|ok`, passer par
  `_check_kobo_legacy_canonical_owner` (veto négatif sur les collisions)
  et **ne pas appeler `_mirror_backup_row`** ;
- sur une première réception depuis `acquiring` ou sur une édition
  sans archive précédente, conserver temporairement le miroir W1 pour
  ne pas interrompre la seule voie de reprise 0.7 restante ;
- si une `SourceReference` typée existe déjà pour
  `credential/edition`, le refus antérieur prend toujours priorité ;
- les autres fournisseurs, révisions et types de documents ne changent
  pas de comportement.

Aucun nouveau droit, aucune suppression d'archive, aucun GET ou
raccourci vers `OriginLink`. Une première livraison 0.7 **reste une
projection provisoire**, pas un `CompletedDelivery` canonique :
le cutover final de W1 exige toujours W2/D1–D4 et l'intégration
d'une attestation A→B.

## Vérifications

`tests/test_core_p0_3196_w1_completed_archive_no_reprojection.py`
utilise SQLite v14 et contrôle que :

1. une archive `complete` ou `ok` insérée historiquement, avec
   connexion et emprunteur Kobo exacts, peut actualiser ses descriptions
   sans créer `entitlements/acquisition_ledger/artifacts/placements` ;
2. une première livraison `acquiring→complete` garde la compatibilité
   W1, mais son rejeu `complete→complete` ne change plus les tables B ;
3. une tentative de changement d'emprunteur d'un reçu complet reste
   rejetée atomiquement, sans mutation de B ni de l'archive.

Le contrat cible l'**écriture canonique** et complète la réduction du
compteur de présence W2 de #4990, sans modifier ses fichiers Core-A.
