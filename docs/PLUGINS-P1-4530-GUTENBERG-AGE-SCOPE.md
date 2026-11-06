# P1 #4530 — Gutenberg : sujets et variantes EPUB sans preuve d'âge (06/11/2026)

## Contrat vérifié hors réseau

Le flux OPDS Gutenberg sépare les catégories `LCSH` (sujets) et `Bookshelf` (rayons). La normalisation sélectionne une variante EPUB (avec ou sans images) par identifiant Gutenberg, puis conserve les sujets de **cette variante**. Un titre identique ne justifie aucune jonction entre numéros Gutenberg distincts.

Les tests synthétiques couvrent deux variantes EPUB d’un même numéro avec sujets d’âge contradictoires (« From 8 years » et « 13-99 »), ainsi qu’un deuxième numéro portant le même titre. Les libellés sont conservés seulement comme sujets/rayons de la variante retenue ; aucune clé `recommended_age`, `youth_age_ranges`, `attested_age_ranges`, `attested_age_status` ou `public_age_range_label` n’est produite. Un élément XML `audience` supplémentaire non qualifié n’autorise pas davantage cette conversion.

| Source | Notices réelles d’âge examinées | EPUB synthétiques | Âges numériques attestés par édition |
| --- | ---: | ---: | ---: |
| Gutenberg OPDS | 0 | 3 entrées, 2 numéros, 2 préférences de variante | 0 |

**Aucune couverture réelle démontrée.** Le sujet documentaire « Children's Bookshelf » n’est ni une borne d’âge ni une certification de lecture d’une édition exacte. La qualification d’un véritable champ d’âge devra apporter une preuve publique par numéro/édition/format et par langue ; elle ne peut être déduite d’un rayon, d’un titre, d’une date ou d’une préférence d’image. Aucun appel HTTP, collecte massive, téléchargement, modification de droits ou de consolidation C (#4552) dans cette tranche.
