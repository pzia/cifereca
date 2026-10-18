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


## Requêtes mixtes et audio désactivé — 1.2.21 (#1917)

Un filtrage explicite qui demande à la fois `epub` et `mp3` alors que
`include_audio=false` ne recherche que les EPUB. La réponse conserve
ses notices et un éventuel zéro valide **pour la seule branche ebook**,
mais annonce maintenant `partial_results : Gallica : branche audio
désactivée par configuration`. L'absence d'une recherche audio ne peut
pas être présentée comme un zéro complet sur les deux formats demandés.

Un filtre `epub` seul ou une recherche sans filtre explicite ne crée
pas de fausse alerte audio quand le fournisseur est configuré pour
désactiver cette branche. Si la branche EPUB échoue, l'erreur de service
existante reste levée (pas de faux succès partiel) ; aucune route audio
supplémentaire n'est appelée et aucune configuration n'est modifiée.
La recherche audio explicite **seule** reste classée
`unsupported_content_type` sans HTTP, comme dans 1.2.19.

Tests de branches simulées hors réseau : requête mixte réussie, erreur
de branche, audio activé et effacement de l'alerte à la recherche
suivante. La disponibilité réelle des services Gallica reste à
valider séparément.
