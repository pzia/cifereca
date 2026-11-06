# P1 #4530 — frontière âge du catalogue Kobo (06/11/2026)

## Constat technique

L'adaptateur `cifereca_kobo_catalog.py::normalize_item` copie les métadonnées JSON brutes du catalogue. Aucune propriété structurée d'âge ni correspondance exacte support/édition n'a été qualifiée sur une réponse Kobo réelle dans cette tranche. Le nom d'une clé fournisseur ne peut pas valoir preuve canonique du Core.

L'adaptateur écarte donc les clés **réservées à une preuve normalisée** si elles viennent directement du catalogue non qualifié : `recommended_age`, `youth_age_ranges`, `attested_age_ranges`, `attested_age_status`. Les champs descriptifs non autoritaires (par exemple `genre`, `category`, `typicalAgeRange`) restent disponibles **sans inférence numérique ni attribution à l'édition achetée**. Cela s'applique aux projections publiques et privées via la même normalisation et ne change ni les droits ni les acquisitions.

Tests synthétiques : absence de promotion même si l'amont fournit littéralement ces clés, avec `owned=true/false`; absence d'inférence depuis titre ou catégorie jeunesse. Aucun endpoint réseau ou API supplémentaire.

## Échantillon et limites

| Source | Notices réelles vérifiées | ISBN numérique exact attesté | Âge numérique par édition |
| --- | ---: | ---: | ---: |
| Kobo public/compte, cette tranche | 0 | 0 | 0 |
| Fixtures synthétiques d'injection de clés | 2 modes de propriété + cas texte | non applicable | aucune inférence |

Le tableau **ne représente pas une estimation de couverture Kobo**. Il reste à qualifier sur échantillon réel borné et par langue les éventuelles propriétés explicites et leurs relations d'édition/support ; puis à arbitrer leur source, format et propagation dans #4552. Ne pas transformer Kids Store, genres, titres ou ONIX théorique en preuve de tranche numérique.
