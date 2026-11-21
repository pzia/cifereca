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


## Complément du 20 novembre 2026 — quatre éditions électroniques

Source : qualification documentaire consignée dans [#4530](https://github.com/pzia/cifereca/issues/4530),
via le rendu indexé du Catalogue général BnF. Ce n'est pas une capture
SRU/MARC ni un corps HTML brut. Les quatre notices distinctes indiquent
« Texte électronique », EPUB, ressource dématérialisée, BoD, 2026,
métadonnées fournies par l'éditeur, et un public destinataire d'au moins
12 ans. Maximum non indiqué. Échantillon ciblé d'un seul éditeur.

| Notice | ISBN de l'édition électronique | Référence BnF | Qualification |
|---|---|---|---|
| Humanisation | 9782322591763 | [cb48748148t](https://catalogue.bnf.fr/ark:/12148/cb48748148t) | 12 ans minimum, maximum inconnu ; preuve documentaire indexée |
| Les bois renaissants | 9782322591367 | [cb48708439v](https://catalogue.bnf.fr/ark:/12148/cb48708439v) | idem |
| Campus Stories Les étoiles du lycée | 9782322592906 | [cb48736286z](https://catalogue.bnf.fr/ark:/12148/cb48736286z) | idem |
| Campus Stories Amour en première ligne | 9782322592913 | [cb487362906](https://catalogue.bnf.fr/ark:/12148/cb487362906) | idem |

**Frontière de réception** : l'âge documentaire concerne ici des éditions
numériques identifiées, contrairement aux échantillons imprimés du 06/11.
Cependant les deux requêtes SRU ciblées sur le premier ISBN (schémas
unimarcXchange / intermarcXchange, version 1.2, maximumRecords=5) n'ont pas
fourni de réponse XML exploitable au contrôle. Trois ouvertures directes
de fiches ont également expiré côté outil. Aucun statut HTTP fournisseur
confirmé, aucune absence de champ ou d'enregistrement déduite.

Pas d'autorité CNLJ ni de code JAg reconstitué depuis ce rendu ; ne pas
fabriquer de fixture XML à partir de la page indexée. La transmission
positive à C reste conditionnée à une preuve structurée qualifiée pour
la même édition. En attendant, aucun YouthAgeRange numérique ni filtre
positif n'est émis à partir de ces quatre références. Les autres
fournisseurs du tableau restent dans leur état de preuve précédent ;
BiblioAccess La Rochelle : âge numérique exact non attesté, ne pas le
déduire d'une catégorie ou d'une fiche JSON-LD générique. Les captures
externes résiduelles sont suivies dans #6875. Cette mise à jour ne change
pas le contrat du parseur, le transport, les droits ou la politique.
