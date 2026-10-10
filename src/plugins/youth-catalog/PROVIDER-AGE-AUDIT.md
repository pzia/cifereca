# Audit des métadonnées d’âge des providers existants

Audit réalisé pour #741 avant toute modification des providers.

| Provider | Métadonnées actuellement projetées utiles | Âge jeunesse explicite projeté | Décision |
| --- | --- | --- | --- |
| Paris | formats, métadonnées Syracuse normalisées, disponibilité | non | ne rien inférer ; qualifier la donnée amont avant extension |
| Kobo | langue, formats et métadonnées catalogue | non | ne rien inférer |
| Gallica | sujets, langues, formats bibliographiques | non | sujets ≠ tranche d’âge |
| BnF Catalogue | sujets, langues, formats, droits | non | qualifier un éventuel champ source avant modification |
| Gutenberg | sujets, bookshelves, langue | non | bookshelves/subjects ≠ âge |
| Numilog | bibliothèque possédée et formats de livraison | non | aucune tranche attestée dans le parseur actuel |
| Baen | produit public / Free Library | non | aucune tranche attestée |
| EbooksGratuits | catégories et genres | non | catégorie/genre ≠ âge |

Conclusion : aucun des huit providers audités ne doit être modifié dans ce groupe. Le plugin Jeunesse peut consommer leurs données bibliographiques, mais une tranche d’âge ne doit être ajoutée que lorsqu’un champ amont explicite est qualifié source par source.

Les sources éditeur encore prises en charge sont Milan et École des loisirs ; chaque âge conserve son type, sa provenance et son libellé attesté.
