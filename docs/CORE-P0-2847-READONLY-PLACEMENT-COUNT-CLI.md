# P0 #2847 — Compteurs de Placements en lecture seule

Une entrée CLI installée avec cifereca expose les compteurs expurgés des Placements SQLite dont le chemin physique est directement vérifié. Elle ne répare, ne relocalise, ne télécharge et ne supprime rien.

## Commande opérateur

Depuis le Python de l'installation cifereca :
```sh
python -m cifereca.core.missing_placements_cli --identity ID_DU_PROFIL
```

Les chemins de configuration et de base sont résolus sans créer de dossier depuis les emplacements XDG de l'application. Pour une installation isolée :
```sh
python -m cifereca.core.missing_placements_cli \
  --identity ID_DU_PROFIL \
  --config /chemin/absolu/vers/config.json \
  --database /chemin/absolu/vers/library.sqlite3
```

`ID_DU_PROFIL` est l'identifiant technique dans `identities[].id`, pas le libellé affiché. La commande ne l'imprime pas. Elle utilise la configuration existante sans invoquer `Config.load()` (qui peut faire un chmod) et dérive toutes les racines déclarées pour ce profil, y compris un répertoire Kobo séparé. Toute racine absente ou chemin hors portée reste `unavailable`/`out_of_scope`, jamais `verified_missing`.

## Sortie et statut

L'unique sortie standard est un JSON d'agrégats, sans titre, ISBN, chemin, identifiant de livre, compte fournisseur, token ou contenu de fichier.

- `status=complete` et code retour 0 : instantané SQLite complet et tous les Placements de l'identité classés physiquement. `counts.verified_missing` est un constat local ENOENT, pas une autorisation de suppression.
- `status=incomplete_scan` et code retour 2 : aucune conclusion de « zéro manquant » ; vérifier `sqlite_coverage`, `path_coverage`, `counts.unavailable`, `counts.out_of_scope`, `counts.unexamined` et les `reasons` fermés. `counts=null` signifie que la lecture de SQLite n'a pas été attestée.
- `missing_by_sqlite_state` distingue l'ancien état enregistré SQLite du constat physique ; une ligne `present` dont le fichier manque n'est pas effacée par l'audit.

Le parcours réutilise `read_library_snapshot` (copie en mémoire et lecteur WAL isolé), `scope_evidence`, `configured_identity_roots` et `inspect_stored_paths` (double probe no-follow). Aucun `Storage(...)`, service, plugin, réseau, migration, GTK, écriture SQLite ni export de chemin.

## PU et clôture

Ne demander une recette que lorsque cette commande est installée et accessible (`python -m ... --help`). Produire un rapport par profil sur la bibliothèque réelle et ne transmettre que les compteurs expurgés, le code retour et la version installée. Qualifier ensuite les cas réels avec les opérateurs déjà livrés, sous confirmation individuelle. Les 273 placements de la fixture historique ou un zéro issu d'un scan incomplet ne justifient aucune clôture.

Tests de l'entrée sur SQLite v14 et fichiers synthétiques :
```sh
PYTHONPATH=src python -m pytest -q tests/test_core_p0_2847_missing_placements_cli.py
```
