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
  puis exécuter le **préflight en lecture seule** de
  `_mirror_backup_row(..., archive_only=True)` : conservation de
  tous les contrôles du reçu canonique (`CompletedDelivery`
  JSON unique/borné, statut cohérent, propriétaire exact), **sans**
  entrer dans ses `INSERT/UPDATE` de droits, artefacts, placements,
  catalogue ou acquisitions ;
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

## Échec de la première CI et correction

Le premier HEAD `6f52057` a échoué sur **3 tests** (18 626 réussis) :
le retour anticipé supprimait aussi deux contrôles négatifs d'un
journal `canonical.completed-delivery.v1` malformé/contradictoire, et
un ancien test attendait encore la réécriture du placeholder canonique
sur une archive sans preuve A→B. La voie finale réutilise la phase de
validation déjà présente dans `_mirror_backup_row`, en mode
`archive_only` qui court-circuite uniquement ses mutations positives.
Le test de catalogue conserve désormais sa projection canonique initiale,
son propriétaire et son SHA, tout en acceptant la mise à jour descriptive
de l'archive 0.7. Les tests de refus canonical JSON restent inchangés.


## Complément : transitions post-`complete` (08/11/2026)

Le premier cutover W1 conservait encore un repli positif si un reçu
Kobo **déjà** `complete|ok` passait ensuite dans une autre catégorie
(`acquiring`, `error`, `pending_acquisition`, `reconcile_required`
ou assimilée). Il pouvait alors reprendre `_mirror_backup_row` et
modifier des droits/acquisitions/placements 0.8 sur la seule autorité
du checkpoint 0.7.

L'état `complete|ok` **avant l'UPSERT** suffit désormais à marquer
**toute transition ultérieure** archive-only, sous `BEGIN IMMEDIATE`.
Le checkpoint 0.7 reste mis à jour pour la reprise/diagnostic, et le
préflight négatif vérifie toujours l'owner, le journal JSON et la
cohérence du CompletedDelivery. Aucune nouvelle projection 0.8
n'est permise, même si le statut, la révision, le SHA ou le chemin
historique sont modifiés. La première arrivée `acquiring→complete`
sans état `complete` antérieur conserve le contrat de compatibilité
W2/ W1 du jalon précédent.

La recette paramétrique couvre neuf statuts après une première
projection W1 et six transitions depuis une archive `complete|ok`
préexistante sans preuve B ; elle affirme l'immuabilité exacte de
`entitlements/acquisition_ledger/artifacts/placements/catalog_entries/
source_references/origin_links`, tout en conservant les lignes 0.7.
