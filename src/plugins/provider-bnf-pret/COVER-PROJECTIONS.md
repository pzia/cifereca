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
