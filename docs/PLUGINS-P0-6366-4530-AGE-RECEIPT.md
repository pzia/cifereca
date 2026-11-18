# #6366 / #4530 — réception documentaire des preuves d'âge

Revue du 17 novembre 2026 des preuves **déjà conservées dans le dépôt**,
sans nouvelle collecte. Les résultats positifs de BnF ci-dessous sont
documentaires, et non des âges autorisés pour un livre numérique.

| Source | Corpus réel antérieur | Verdict |
|---|---|---|
| BnF SRU | [9 extraits du 06/11](PLUGINS-P1-4530-BNF-SRU-OBSERVED-AGE.md) | 5 réponses positives, 3 ARK distincts imprimés ; UNIMARC 333$a + 2=CNLJ, INTERMARC 833$m. ISBN numérique distinct : zéro sur la requête. Pas de preuve d'âge numérique. |
| Numilog | [4 fiches produit réelles](PLUGINS-P1-4530-NUMILOG-PUBLIC-SAMPLE-20261106.md) | 4 EAN EPUB distincts du papier ; 2 mentions de collection, 2 descriptions, 0 champ d'âge numérique qualifié ; JSON-LD non vérifié. |
| Kobo | [frontière de normalisation](PLUGINS-P1-4530-KOBO-AGE-BOUNDARY.md) | 0 notice réelle dans cette qualification ; clés canoniques injectées rejetées, unverified. |
| Paris CATALOGUE/EBOOKS | [facettes](PLUGINS-P1-4530-PARIS-AGE-FACETS.md) | échantillons d'âge synthétiques uniquement ; facettes de page non transposables, unverified. |
| Gallica Atom/SRU | [contrôles](PLUGINS-P1-4530-GALLICA-AGE-SCOPE.md) | contrôles synthétiques homonymes, audio et sujets, unverified. |
| Gutenberg | [contrôles](PLUGINS-P1-4530-GUTENBERG-AGE-SCOPE.md) | variantes EPUB et rayons synthétiques ; unverified. |
| EbooksGratuits | [contrôles](PLUGINS-P1-4530-EBOOKSGRATUITS-AGE-SCOPE.md) | catégories OPDS synthétiques ; unverified. |
| BnF-Prêt | pas de preuve d'âge numérique par édition dans la qualification existante | unverified ; aucun transfert BnF imprimé. |
| Baen | [contrôles](PLUGINS-P1-4530-BAEN-AGE-SCOPE.md) | aucun champ qualifié ; hors #1917, frontière d'âge seulement. |

Ce tableau n'est pas une mesure de couverture. Les échantillons réels
ciblés sont antérieurs et non rééchantillonnés ; les tests de mutations
restent synthétiques. Un zéro pour une requête n'est pas une absence
universelle d'âge. Ne pas agréger les deux formats MARC comme deux éditions.

## Garde du pilote BnF

Le parseur de preuve brute reste en place : une page partielle peut fournir
des observations brutes bornées. La qualification numérique exige désormais
une réponse complète sans indication de page suivante, aucun emballage
ininterprétable, aucun ISBN propre indéterminé ou ambigu pour la cible et
aucun champ d'audience invalide ignoré. Les sous-champs invalides, vides,
trop longs ou imbriqués ne peuvent plus disparaître avant la vérification
du champ complet. Les 329 critiques restent distincts. Les valeurs CNLJ
déjà observées restent les seules autorisées, sans autorisation d'un
YouthAgeRange Core. Les tests réutilisent les cinq réponses positives
existantes et ajoutent des mutations négatives.

Choix conservateur : une notice secondaire ininterprétable dans la même
réponse bloque aussi la qualification unique, même si l'extrait brut de
la notice cible est lisible. Pas de modification du transport SRU,
nouveau parseur, droit, source C, schema ni projection publique.
Reste la preuve d'édition numérique exacte et le raccordement #4552/#2385.
