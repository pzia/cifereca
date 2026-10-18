# Gallica — portée des recherches par formats

## P1 #1917 — 1.2.19

Quand le filtre de formats ne correspond à aucune branche active (p. ex.
`include_audio=false` avec seulement `mp3`), le provider **n'effectue aucun
HTTP** et conserve `[]` pour compatibilité avec le moteur fédéré.
Il expose cependant `last_search_warnings = ["unsupported_content_type :
aucune branche Gallica disponible pour les formats demandés"]` : ce vide
n'atteste aucune recherche réussie ni absence de documents au catalogue.

Si une branche reste disponible, les recherches et les avertissements
partiels existants sont inchangés. Chaque nouvelle recherche efface les
anciens avertissements. Tests synthétiques dans `tests/test_search_capabilities_p1.py`.


## Pagination OPDS/SRU — 1.2.20 (#1917)

`page` est désormais un entier strict, zéro-indexé, compris dans `0..100`
(`max_pages=101` dans `ProviderSearchCapabilities`). Un index négatif,
booléen, fractionnaire, textuel ou hors limite donne
`ProviderCapabilityError('Gallica : invalid_page')` avant toute branche
OPDS/SRU. Les branches conservent leur `startRecord`/`startIndex` existant,
sans clamp des requêtes invalides. Aucun HTTP additionnel n'est ajouté.
