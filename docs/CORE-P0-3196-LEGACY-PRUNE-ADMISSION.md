# Core P0 #3196 — admission du prune historique Kobo

`Storage.backup_prune_missing(provider, credential, seen_item_ids)` **ne modifie plus les droits**, même si l'identité, le compte et le manifest fournisseur sont parfaitement concordants. `backup_items` est une projection transitoire v0.7 et n'atteste ni la complétude d'un inventaire canonique, ni un retour de prêt, ni une révocation d'achat. Les entitlements miroirs, SourceReferences, OriginLinks, Artifacts, Placements et reçus historiques sont conservés. L'ancien champ de bilan `entitlements_inactivated` reste présent avec la valeur **0** pour la compatibilité des consommateurs.

## Frontière de prune

Le synchroniseur Kobo ne peut éliminer les lignes de **projection** absentes qu'après son inventaire admis comme complet. À la frontière SQLite, les IDs doivent rester un `set`, `list` ou `tuple` de 100 000 éléments au plus, exclusivement des chaînes non vides de 1 024 caractères au plus sans NUL/CR/LF. Un manifeste partiel ou malformé (`None`, chaîne, dictionnaire, itérateur ou mélange invalide) est rejeté **avant toute mutation**. Un ensemble vide valide peut vider la projection sans jamais toucher aux droits.

L'opération est atomique sous `BEGIN IMMEDIATE`. Les fichiers sont seulement constatés pour le compteur `local_preserved`, jamais effacés ni promus en preuves vérifiées. Un renouvellement d'acquisition doit être attribué par des preuves positives typées `#3171` et une éventuelle conclusion négative dépend de son **propre** contrat exhaustif et scoper `#3197`, non d'un prune v0.7.

## Recette / limites

`tests/test_core_p0_3196_prune_exact_right_scope.py` couvre les achats et prêts sous owner exact, les collisions, les rebonds d'identité/compte, la conservation des reçus et les manifestes invalides. Il ne s'agit ni d'une migration des writers `backup_put`, ni d'une preuve fournisseur live. La migration et reprise crash 0.7/0.8 restent nécessaires. Aucun DROP TABLE.
