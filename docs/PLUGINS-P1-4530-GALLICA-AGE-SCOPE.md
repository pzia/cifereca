# P1 #4530 — Gallica : limites des âges d'édition (06/11/2026)

Les projections publiques Gallica sont traitées séparément : entrées **OPDS/Atom** (livres numériques) et notices **SRU/Dublin Core** (audio). Les catégories OPDS et les sujets/descriptions SRU sont des informations bibliographiques descriptives. Une étiquette comme « Dès 8 ans » ou « 13-99 » **n'atteste pas** une borne d'âge applicable à un ISBN et à un format numériques précis.

Les tests synthétiques `test_p1_4530_gallica_age_evidence.py` vérifient :
- Deux entrées OPDS de même titre et ARK différents conservent chacune leur catégorie sans transmettre l'âge à l'autre édition.
- Une notice sonore SRU avec sujet et description d'âge ne produit aucune preuve numérique.
- Les champs `recommended_age`, `youth_age_ranges`, `attested_age_ranges`, `attested_age_status` et `public_age_range_label` restent absents ; les champs descriptifs ne sont pas convertis.

| Source | Notices réelles d'âge examinées dans cette tranche | Fixtures synthétiques | Preuves d'âge par édition |
| --- | ---: | ---: | ---: |
| Gallica OPDS | 0 | 2 livres homonymes | 0 |
| Gallica SRU audio | 0 | 1 notice sonore | 0 |

**Aucune couverture réelle du catalogue n'est mesurée.** Pas de requête live, pas de réutilisation automatique d'une preuve BnF d'une édition à une autre, pas de conversion d'un sujet jeunesse en borne. Le cas réel d'un ARK/ISBN numérique correspondant exactement et l'échantillonnage par langue restent à qualifier avant consolidation #4552.
