# P1 #4530 — Ebooks libres et gratuits : catégories OPDS et âge (06/11/2026)

## Frontière démontrée (tests sans réseau)

Le COPS/OPDS du fournisseur projette les valeurs `atom:category` en `categories` et `genres`, selon le `scheme` déclaré. Ce sont des **étiquettes bibliographiques descriptives**, pas des tranches d'âge attestées d'une édition ou d'un EPUB précis. L'adaptateur ne doit jamais convertir un rayon « Jeunesse », « Dès 8 ans », « 12-15 » ou « 13-99 » en preuve numérique.

Les tests synthétiques couvrent deux entrées de même titre, aux identifiants OPDS distincts et aux catégories/genres contradictoires, ainsi que des éléments XML d'âge non qualifiés et quatre variantes de libellé. Les valeurs restent propres à chaque entrée et aucun champ `recommended_age`, `youth_age_ranges`, `attested_age_ranges`, `attested_age_status` ou `public_age_range_label` n'est créé. Les propriétés `owned=False` restent inchangées.

| Source | Notices réelles examinées pour l'âge | Notices synthétiques | Âges numériques attestés par édition |
| --- | ---: | ---: | ---: |
| Ebooks gratuits OPDS | 0 | 2, plus libellés paramétrés | 0 |

**Aucune couverture réelle n'est mesurée.** La présence d'un genre ou d'une catégorie dans un flux n'autorise ni transfert d'âge entre éditions homonymes ni extrapolation depuis la classification. L'échantillonnage réel par ISBN/support/langue est à qualifier séparément ; consolidation #4552 non modifiée. Aucun appel réseau, téléchargement, droit d'accès ni placement dans cette tranche.
