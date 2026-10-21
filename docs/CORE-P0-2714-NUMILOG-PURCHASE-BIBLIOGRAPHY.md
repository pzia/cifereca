# P0 #2714 — voir le titre et l’auteur d’un achat Numilog non rattaché

## Constat reproductible

Le fournisseur Numilog conserve normalement, lors de la synchronisation
de la bibliothèque personnelle, un `entitlement.metadata` indiquant
`source=numilog.authenticated_library`, `title`, `author` et
`media_format`. L'ancien snapshot privé de la réconciliation
supprimait entièrement ces libellés du droit : **il ne conservait
que la source attestée**. Une éventuelle notice bibliographique
`catalog_entries`, jointe exactement par identité/fournisseur/item_id,
pouvait encore fournir titre et auteur, mais en l’absence de notice ou
en cas de notices multiples, l’UI retombait **silencieusement** sur
`item_id`, bien que l’achat détenait déjà son titre.

## Projection sûre — lecture seule

- Le snapshot de la SQLite locale conserve maintenant en mémoire Core
  **uniquement** les champs descriptifs bornés
  `display_title` (256 caractères),
  `display_authors` (256 caractères depuis `author` ou `authors`),
  et `display_media_format` (valeurs fermées : EPUB/KEPUB/PDF/ACSM)
  en provenance de l'entrée `entitlements.metadata_json`. Le JSON
  original reste supprimé du snapshot ; **les URLs, mots de passe,
  tokens, reçus et données arbitraires n'en sortent pas**.
- La fiche privée d'un droit non rattaché affiche **Titre,
  Auteur(s), Format déclaré, Fournisseur déclaré, Édition fournisseur,
  État du droit** et la **provenance du titre et des auteurs**.
- Si **une seule notice locale** de même identité, fournisseur et
  édition possède un champ descriptif, il est utilisé comme
  *description de catalogue non probante*. Pour un champ manquant,
  le droit lui-même peut fournir son libellé descriptif ;
  les provenances restent affichées **champ par champ**.
- Si **0 notice**, affichage explicite : « Aucune notice locale de
  cette édition exacte » et repli sur les métadonnées d'achat
  enregistrées. Si **N>1 notices**, aucune notice arbitraire n'est
  choisie : le repli ne peut utiliser que le droit de la même
  identité/connexion/compte/édition.
- Sans titre ni auteur fiable, **« Non renseigné »** avec source
  indisponible, suivi de l’identifiant d’édition, pas d’invention
  à partir d'ISBN, nom de fichier, hash ou placement.

**Aucune promotion de preuve** : ces libellés ne prouvent ni possession
d'un fichier, ni lien d'acquisition/artefact/placement. Les
`confirmed_link`, `metadata_candidate`, graphes, recherches
bibliographiques et droits restent inchangés. Cette tranche
n'autorise **aucun rattachement** et n'ajoute aucun accès réseau,
réparation, écriture SQLite ou fichier.

## Recette

Sur une bibliothèque de 6 EPUB et 3 droits Numilog non vérifiés,
ouvrir la fiche privée d'un achat dont la notice est absente,
présente, ambiguë ou malformée ; vérifier titre/auteur/format
et source explicite, sans divulguer de comptes étrangers.
Toutes les projections publiques `Audit.redacted()` restent
strictement privées et sans libellés. L'examen des 200 fichiers
non liés / la sélection directe / préflight sûr restent à livrer
séparément sous #2714.
