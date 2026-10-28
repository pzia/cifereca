# Core P0 #3196 — désautoriser `backup_items` lors de l'indexation

## Tranche de migration sans destruction

Dans `LocalCatalog.index_path()` et `LocalCatalog.scan()`, un chemin
`backup_items.local_path` ne fournit désormais **aucun** `provider_id`,
`source_item_id`, titre attribué ni auteur. Même un enregistrement legacy unique,
parfaitement égal au chemin et à l'identité, n'atteste ni un achat, ni un prêt,
ni une origine `provider`. Le parcours ne consulte plus
`Storage.backup_find_by_local_path()` pour créer ces champs.

La portée explicite du caller, si elle est fournie, reste contrôlée contre
l'existant ; un `Placement` déjà enregistré conserve son identité, son
fournisseur et son édition. Le scan revalide les octets et n'efface ni les
références acquises ni les métadonnées de catalogue précédemment conservées.
Les `backup_items` d'origine ne sont ni détruits ni réaffectés.

## Réception hors réseau

`tests/test_core_p0_3196_catalog_legacy_is_not_origin.py` :
- index ciblé et scan complet, un seul backup historique correspondant ;
- aucune attribution de fournisseur/édition dans le nouveau Placement ;
- backup conservé, aucun nouveau droit ;
- Placement indépendant déjà connu et divergent : son propriétaire,
  fournisseur et édition ne sont jamais remplacés par le backup.

## Limites et prochaine tranche P0

Cette PR **ne clôt pas #3196** : `kobo_backup`, les API
`backup_put/get/list/prune`, les historiques de reprise et certaines
projections/maintenance lisent encore l'ancien schéma. Leur migration doit
préserver les états et preuves 0.7/0.8 avec compte/credential exacts. Aucune
suppression de table avant recette de migration versionnée et vérification
de la reprise après crash.

Références : [#3171](https://github.com/pzia/cifereca/issues/3171),
[#3196](https://github.com/pzia/cifereca/issues/3196).
