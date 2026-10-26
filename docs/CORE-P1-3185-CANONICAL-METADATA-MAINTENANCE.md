# Core P1 #3185 — chemins actifs dans les métadonnées canoniques

## Incrément conservateur

La suppression `library.maintenance.delete_duplicate` et le renommage
`Storage.relocate_local_path` sont déjà protégés contre certaines références
de catalogue, ledger, sauvegarde, variante et déclaration. Cette tranche ajoute
le contrôle des champs JSON des tables `entitlements`, `artifacts` et
`placements`, susceptibles de conserver une référence indépendante au chemin
physique supprimé ou renommé.

- Parcours **lecture seule** de trois tables **autorisées explicitement**.
  Budget : 100 000 enregistrements/table et 1 Mio de JSON/enregistrement.
- La correspondance est **la chaîne exacte de chemin**, même imbriquée dans un
  dictionnaire ou une liste ; jamais SHA, ISBN ou titre.
- Un JSON impossible à interpréter, un excès de budget ou une profondeur
  excessive entraîne un **refus conservateur**, pas une preuve d'absence.
- Pour le doublon : double vérification lors du plan et avant la suppression
  SQLite/FS. Pour le renommage : vérification sous la transaction SQLite,
  avant de déplacer les pointeurs canoniques ; un échec laisse l'ancien chemin
  et les références en place.
- Les métadonnées historiques ne sont **pas réécrites**. Tant qu'un champ
  n'est pas classé `actif` ou `historique`, le Core préfère bloquer une
  maintenance plutôt que casser une référence potentiellement opérationnelle.

## Hors périmètre et suites

Cela **n'implémente pas** la migration/réassociation transactionnelle des
métadonnées, l'entretien des journaux de reprise, le rangement inter-volumes
ni la suppression de placements `missing` (#2847). Les champs historiques
devront recevoir une classification explicite avant la suppression de ce refus
conservateur. Les vrais tests d'intégration utilisateur sont à conduire avec
la recette de #3185.

Fixtures SQLite et fichiers locaux uniquement ; aucun service externe, aucune
mutation de droits ni release.
