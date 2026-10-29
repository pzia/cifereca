# Core P0 #3196 — empêcher un replay 0.7 de réécrire le catalogue canonique

## Cas de régression

`Storage.backup_put()` conserve une projection de compatibilité `backup_items`
avec un miroir vers un `Placement` SQLite `state=unverified` et
`catalog_entries`. Le garde existant sur le `Placement` interdit les
écrasements si celui-ci a été promu, mais **pas** lorsque seule l'entrée de
catalogue a été rafraîchie indépendamment (scan, déclaration, réconciliation)
en gardant son ancien `catalog_entry_id` et son Placement non vérifié.

Un replay du manifeste pouvait alors remplacer le titre, le format,
l'Artifact déclaré et les horodatages du catalogue déjà canonique.

## Contrat

- Le miroir 0.7 peut rafraîchir un ancien Placement uniquement si **toutes**
  ses entrées de catalogue sont encore des placeholders `imported_from=backup_items`,
  `local_state=local`, de même identité, fournisseur et édition.
- L'UPSERT catalogue vérifie à nouveau exactement ce marqueur, le Placement et
  le scope avant toute modification. Tout changement autonome de provenance
  descriptive, de scope ou d'état bloque la mutation physique héritée.
- Le `backup_items` de compatibilité reste rafraîchi ; le ledger peut enregistrer
  la tentative sans acquérir de droit ni établir d'OriginLink.
- La décision et les mises à jour restent sous **la même transaction SQLite**.
  Aucun scan, fichier, réseau, ProviderRightObservation ou consentement n'est
  affecté. Pas de suppression historique.

## Tests

`tests/test_core_p0_3196_legacy_catalog_replay_fence.py` : promotion
descriptive, changement de scope et changement d'état, promotion réelle via
`catalog_entry_put`, et relecture d'un placeholder legacy encore modifiable.
Vérification bit-à-bit des tables physiques/OriginLinks/SourceReferences,
conservation du `backup_items` et absence de fichier présent.

## Limite de réception

Cette tranche protège la coexistence 0.7/0.8/v14. Elle **ne retire pas**
`backup_put`, ne certifie pas l'ancienne base de production et ne constitue pas
la sortie complète de `backup_items`. #3196 et #3171 restent ouverts jusqu'à
migration des écrivains/lecteurs et recette réelle de reprise.
