# Couvertures : recette et frontière de projection (#3104)

Les deux exemples de recette sont « 2050, une Histoire du Futur » et
« La promesse du dragon », sous le profil Hugues. Les tests utilisent ces
titres avec des identifiants et du HTML synthétiques, jamais comme clé de liaison.

Chaîne actuelle : `_loan_cover` accepte une image publique autorisée dans
l'ancre de la ressource exacte de la carte du prêt. `LoanRecord.thumbnail_url`
est conservé par `_read_live_loans_locked` dans `Loan.thumbnail_url` ; les lecteurs
`loans()` et `loan_inventory_snapshot()` conservent ce champ. La sérialisation du
Loan puis sa projection Bibliothèque via le Core conservent également l'URL.
Une observation de droits v1 n'est pas une notice et ne porte pas de couverture.

Bibliothèque possède des candidats supplémentaires : couverture EPUB/cache,
notice locale, métadonnées du droit et catalogue. `loans_controller._render_loans`
transmet directement `loan.thumbnail_url` au `BookListItem` de Mes emprunts.
Il ne résout pas les candidats locaux de Bibliothèque. Une couverture dans
Bibliothèque avec une URL absente du Loan est donc reproductible sans perdre
de champ dans le plugin. Cela ne prouve pas que c'est la cause du cas réel.

`tests/test_two_loan_cover_projections.py` couvre les deux prêts avec images
attestées, les deux lecteurs, la réhydratation du modèle et la projection Core
réelle en lecture seule. Un second scénario reproduit des Loans sans image
alors que la projection Bibliothèque possède des couvertures persistées.
Aucun GET d'image/ACSM, droit nouveau ou acquisition n'est déclenché. Ce n'est
pas une preuve de rendu GTK ni un échantillon réel du DOM BnF.

Handoff A/CoreUI : reproduire sur `cac97e77b` puis comparer, pour chaque couple
credential/compte/loan_id/resource_id exact, (1) image de carte fournisseur,
(2) `Loan.thumbnail_url` en sortie et après relecture du cache,
(3) `metadata.cover_resolution` de la ligne Bibliothèque,
(4) URL passée au BookListItem Emprunts et résultat du chargeur GTK.
Si (2) est vide et (3) vient d'EPUB/notice/droit, la divergence de résolution
est dans le Core. Si (2) est présente, inspecter cache, téléchargement et
décodage GTK. Si la carte fournisseur porte une image non extraite, remettre
à B le balisage exact lié à la ressource pour corriger le parseur.

Le ZIP fourni ici couvre seulement le 29/10 06:59–07:02 Paris, sans valeur
`thumbnail_url` ni DOM de carte permettant de trancher ces branches. Le défaut
terrain reste ouvert ; aucun sélecteur HTML ni enrichissement par titre supposé.

## Mise à jour P1 #4118 — 02/11/2026

Une capture privée BnF du 01/11 a depuis établi des couvertures
`covers.feedbooks.net/item/{id}.jpg?size=…&t=…` dans des cartes de catalogue,
fiches et cartes de prêts actifs. Le parseur `cifereca_bnf_pret_web.py` admet
**uniquement** cette forme de ressource publique (hôte HTTPS exact, deux
paramètres typés, chemins bornés), tout en gardant l'ancienne politique de
`pret.bnf.fr` et `imagedelivery.net`. Les contrôles DOM conservent
l'association à la ressource exacte ; aucune recherche par titre ou ISBN
ni requête image supplémentaire n'est initiée par le provider.

Les tests `test_p1_4118_feedbooks_public_covers.py` couvrent les formats,
les rejets et la projection catalogue/Loan. Ils restent synthétiques :
**l'acceptation d'une URL ne garantit ni un HTTP 200 image ni le rendu GTK**.
La recette des deux miniatures dans Recherche, Détail et Mes emprunts reste
ouverte sous #4118/#3104.

## Validation déterministe de deux couvertures (#4118)

Le test `tests/test_p1_4118_two_cover_pipeline.py` enchaîne le parseur BnF
(recherche puis détail) et le chargeur/cache CoreUI commun avec deux images
PNG distinctes servies par un transport HTTPX simulé. Il vérifie l'association
exacte ressource→URL, deux réponses image 200, deux fichiers différents,
le cache-hit sans troisième GET et le placeholder en l'absence d'image.
Il n'ouvre aucune session lecteur, ne contacte aucun CDN, et ne teste pas
les widgets GTK eux-mêmes.

**Réception terrain encore distincte** : après installation à jour,
ouvrir Recherche BnF avec deux livres dont les images sont présentes dans
leurs cartes officielles, vérifier les miniatures et les mêmes images en
Détail ; vérifier le fallback sur une notice sans image. Une réponse HTTP
image 200 et le décodage GTK ne sont **pas** déductibles du test simulé.
Les comptes, HTML authentifiés et URLs signées restent exclusivement privés.


## P1 #4978 — diagnostic de la source de couverture, strictement en mémoire

Le parseur partagé utilise une classification de candidats d'image **dans la
carte liée à une ressource exacte**. Son vocabulaire est borné : `missing`
(aucune image dans l'ancre exacte), `rejected` (au moins un hôte/chemin/paramètre
interdit), `conflicting` (URL publiques distinctes), `ambiguous` (trop de
candidats) et `observed` (une unique image publique autorisée). Seul
`observed` fournit `thumbnail_url` ; aucune inférence par titre, ISBN,
prêt voisin ou avatar, et aucun GET ajouté. Le même validateur est conservé
pour les cartes catalogue ; les extractions existantes ne changent pas.

Les tests `test_p1_4978_cover_source_diagnosis.py` exercent Feedbooks,
l'hôte BnF, l'ancien hôte public, les URL rejetées, 0/1/N candidats,
les conflits et les liens de ressources voisins. Ces résultats sont
des **observations de balisage**, pas la preuve d'un téléchargement,
d'un rendu GTK, d'un droit d'accès ni de l'appartenance d'un prêt à un compte.

**Handoff pour la matrice inter-vues** : la prochaine étape du lot #4978
doit comparer pour chaque clé exacte de provenance (identité, credential,
compte, prêt, ressource), la source BnF et `Loan.thumbnail_url`,
puis les projections Search/Détail/Bibliothèque/Emprunts et les compteurs
du chargeur/GTK. La classification de la source ne suffit pas à attribuer
une absence d'image au plugin ; si `observed` mais projection perdue,
diagnostiquer l'étape de transformation. Le résolveur CoreUI et la hiérarchie
de couverture locale restent propres au lot #4979, indépendamment du
balisage source BnF.
