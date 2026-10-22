# P0 #2789 — qualifier les anciens chemins SQLite sans modifier la bibliothèque

## Pourquoi 273 chemins absents ne prouvent pas 273 livres perdus

`inspect_stored_paths` vérifie les chemins de **toutes** les lignes
`placements` conservées dans le snapshot SQLite de l'identité active,
y compris les placements historiques portant `state='missing'`.

Le constat `stored_path_missing` signifie seulement qu'un chemin
sous une racine ouverte et contrôlée a produit un ENOENT stable à deux
lectures sous `O_NOFOLLOW`. Il ne prouve ni la disparition physique
du livre, ni un défaut de renommage, ni la perte d'un droit d'achat.

## Projection additionnelle (schéma inchangé)

Le champ additionnel `stored_paths.missing_path_context` contient
uniquement des entiers agrégés :

- `sqlite_present` : lignes de placement encore marquées `present`
  mais dont le chemin local est absent au contrôle ;
- `sqlite_missing` : lignes historiques déjà marquées `missing` ;
- `sqlite_other` : autres valeurs ou états SQLite inconnus ;
- `distinct_paths` : chemins absents distincts observés ;
- `distinct_artifacts_with_id` : artefacts distincts possédant
  un identifiant explicite ;
- `distinct_placements_with_id` : placements distincts possédant
  un identifiant explicite.

La somme des trois classes SQLite doit égaler le compteur existant
`stored_path_missing`. Un résultat interrompu par le budget ne porte
que sur les lignes déjà contrôlées ; aucune extrapolation des lignes
restantes n'est autorisée. Les ensembles de dédoublonnage ne sortent
pas de l'exécution de l'inspecteur : aucun chemin, SHA, titre, auteur,
compte, identité, ID d'artefact ni ligne SQLite ne passe dans
`redacted()`, le plugin Maintenance ou Diagnostics.

Le plugin Maintenance montre la ventilation seulement quand tous
les entiers sont bornés et cohérents avec le total. Les anciens Core
sans ce champ gardent l'affichage existant. L'état `missing` est
**un historique de placement SQLite** : ne pas le convertir en
suppression automatique ni le compter comme un livre actuellement dû.

## Avant une correction éventuelle

Recette à exécuter sur le poste utilisateur, **sans exporter les données
privées** : ventilations des 273 chemins et de leurs artefacts distincts,
recherche des anciens noms renormalisés, distinction entre historique,
rename Core, correction d'extension, renommage externe et panne entre
`os.replace` et `storage.relocate_local_path`.

Tout futur rattachement ou `UPDATE` exige une prévisualisation
individualisée, une preuve de provenance, une revalidation fraîche
fichier/SQLite/identité et une confirmation explicite. Ce diagnostic
n'accorde **aucune** autorisation de mutation.
