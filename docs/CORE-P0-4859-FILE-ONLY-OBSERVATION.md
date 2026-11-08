> [!IMPORTANT]
> **Archive historique — P0 #4859, retrait du 08/11/2026.** La commande `kobo-observe-local` et l'option `file_only=True` sont supprimées du Core/CLI. Un constat d'octets 0.7 ne constitue ni un droit fournisseur ni une livraison attestée. Les instructions ci-dessous sont conservées uniquement comme historique et **ne sont plus exécutables**. Utiliser l'audit consultatif `kobo-audit` ou un relink sur preuves canoniques A→B.

# P0 #4859 — Observation physique Kobo sans autorité historique W4

## Contrat du premier opérateur de remplacement

`core/kobo_local_reconciliation.py::observe_kobo_local_file_only` sépare
l'observation d'un fichier local de l'ancien rapprochement positif
`reconcile_kobo_local`. Il accepte une identité Kobo et un emprunteur
**précisément enregistrés et activés**, une édition revendiquée, une cible
située dans les racines du profil et une paire SHA-256/taille préalablement
connue. L'absence totale de `backup_items` et des entitlements historiques
est admissible pour la seule **preuve d'octets**.

- `confirmation=None` : lecture seule, hash/stat sous racine, token opaque
  lié à la portée, à l'inode et aux métadonnées du fichier.
- Confirmation exacte : même contrôle sous le verrou d'acquisition et
  transaction SQLite, puis écriture **uniquement** d'un journal append-only
  `metadata['core.kobo.local-file-only.…']`.
- Replay : même preuve → `already_observed`. Réemploi du chemin pour un
  autre inode/stat, même SHA inclus → conflit et revue explicite.
- Rebind, connexion désactivée, compte non enregistré, fichier déplacé,
  symlink ou octets altérés → refus sans mutation.
- Les réponses publiques n'exposent ni chemin, compte, empreinte, ni jeton
  de fournisseur. Les archives et fichiers historiques ne sont jamais modifiés.

Le point d'entrée offline `reconcile_existing_profile(file_only=True, ...)`
appelle ce même opérateur avec une connexion SQLite `mode=ro` en prévisualisation,
puis `mode=rw` en confirmation explicite ; aucun démarrage Core/Plugin,
aucun réseau. Le dispatch est opt-in et strictement booléen : il n'altère
pas la sémantique existante de `reconcile_existing_profile`.

Le journal contient obligatoirement
`authority='historical_local_bytes_only'`,
`rights_attested=False`, `canonical_delivery_attested=False`.
**Il n'est pas une source A, une livraison B, un droit, une autorisation
de GET, un OriginLink ou un Placement.**

L'API `reconcile_kobo_local` refuse maintenant toute mutation historique,
et l'ancien exécuteur positif réside **uniquement dans `tests/`** pour la
recette de non-régression des journaux. L'observation file-only reste
indépendante de ces archives ; ce
premier opérateur ne suffit donc pas à fermer #4859 ni #3196.

## Recette

`tests/test_core_p0_4859_kobo_file_only_observation.py` prouve les refus,
la révocation de portée, le changement d'inode, l'absence d'archives,
l'idempotence, le token d'aperçu et l'invariance des tables d'acquisition,
de droit, d'origine, de placement et de catalogue.

**Suite du cutover** : utiliser cette observation séparée des anciennes
mutations pour les cas sans source A, puis supprimer les écritures positives
de W4/undo seulement après livraison des cas ayant une preuve fournisseur
A→B indépendante. Le parent #3196 reste pilote de `storage.py`/W1.
