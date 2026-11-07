# P0 #4858 — W2 : fin de l'autorité positive de « complete » 0.7

## Ancien comportement

Dans `_skip_manifest_item`, une ligne `backup_items.complete` portant
la même révision que le manifeste et un `local_path` contenant des octets
au SHA/taille attendus produisait encore `summary["unchanged"] += 1`.
Cette preuve n'attestait pourtant ni `SourceReference` fournisseur,
ni reçu `CompletedDelivery`, ni `OriginLink`, ni `Placement` exact
dans le profil courant. Le `backup_put` de rafraîchissement pouvait en
outre déclencher le miroir W1 vers les tables 0.8.

## Après ce jalon

Lorsque le contenu historique est lisible et son SHA correspond :

- aucun retour positif `unchanged` n'est accordé au seul archive 0.7 ;
- `reconcile_pending` et `reconcile_skipped` indiquent que la
  migration canonique/revue explicite manque encore ;
- aucun nouveau `backup_put` du chemin « checksum inchangé » :
  le dernier reçu historique, son chemin et sa révision restent intacts ;
- **aucun GET de remplacement**, aucun nouveau droit, OriginLink ou
  Placement inféré à partir d'un SHA, même à octets identiques ;
- les contrôles existants des fichiers absents/modifiés, symlinks,
  périmètres de profil et révisions différentes demeurent des veto.

Une réception fraîche par l'exécuteur legacy initial reste présente dans
W2 en attendant son remplacement par A→B ; ce jalon **ne revendique pas**
la suppression globale de W1, du worker W2 ni la clôture #4858/#3196.
Les archives historiques demeurent accessibles pour l'opérateur de
retirement attesté, tandis que les compteurs W2 ne représentent plus
une présence B prouvée par une seule archive.

Tests : `tests/test_core_p0_4858_w2_legacy_complete_not_canonical_presence.py`
et adaptation des anciennes assertions qui confondaient
`SHA historique vérifié` et `origine canonique publiée`.
