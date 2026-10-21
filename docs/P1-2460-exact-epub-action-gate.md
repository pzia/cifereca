# P1 #2460 — offre EPUB explicite avant l’action « Télécharger »

La capacité `direct_download` d'un **fournisseur** dit seulement que le
plugin possède une méthode de téléchargement. Elle ne prouve **pas**
qu'une notice précise dispose d'un EPUB téléchargeable.

## Comportement

Dans `LibraryService.can_direct_download`, le cas
`source_provider=ebooksgratuits` exige, en plus de la capacité
`direct_download`, les trois preuves **de la notice consultée** :

- `downloadable is True` explicitement ;
- `download_url` textuelle et non vide ;
- URL non altérée par des espaces avant ou après.

Un ancien résultat hors ligne `known_catalog_stale` n'expose jamais
cette action, même s'il contient encore une ancienne URL.

La source EbooksGratuits produit déjà `downloadable=bool(epub)` à partir
d'un lien OPDS `application/epub+zip` explicite. Le plugin vérifie encore
l'URL, l'origine, la redirection et le conteneur **à l'exécution**, sous
clic de l'utilisateur. La simple présence d'un fournisseur compatible
ou la mention « Distant » ne suffit pas.

Ce garde-fou **ne force pas** les lignes « Distant » de la bibliothèque
à devenir « Numérique », ne réutilise aucun achat/droit et ne requalifie
pas les caches inconnus. Un enrichissement explicite reste nécessaire
lorsque le lien exact manque. Les autres fournisseurs ne changent pas.

## Recette

Fixture notice distante sans offre EPUB → aucune action « Télécharger » ;
même notice après recherche fraîche fournissant un lien EPUB OPDS explicite
et `downloadable=True` → action possible, téléchargement toujours manuel.
Tester également un résultat stale, un fournisseur voisin, une URL vide
ou malformée, un plugin désactivé, et le passage hors-ligne.

Le **signal utilisateur** « Distant → Numérique après enrichissement »
requiert encore une comparaison sur deux notices réelles : fournisseur,
identité, `medium`, `format`, `downloadable`, présence booléenne du lien,
provenance catalogue, état du cache et phase de rafraîchissement (sans
publier l'URL elle-même).
