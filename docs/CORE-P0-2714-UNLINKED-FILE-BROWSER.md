# P0 #2714 — retrouver un EPUB non lié sans suggestions bibliographiques

## Problème terrain

Sur l'installation réelle, l'audit partiel signalait **200 fichiers
sans lien confirmé** et **408 droits sans fichier vérifié**, mais la
liste de candidats par édition était vide. Le précédent écran
« Comparer tous les fichiers non liés » affichait **un seul fichier
par flèche**, sans filtres structurés ni choix direct : impossible de
retrouver efficacement un EPUB parmi les centaines de fichiers.
Une suggestion ISBN/SHA absente ne prouve pas qu'un livre est absent.

## Nouveau parcours — uniquement privé / lecture seule

Sous un droit non lié :
- si des `metadata_only` exacts sont trouvés, leur vue existante
  reste accessible. Le bouton « Comparer tous les fichiers non liés »
  bascule dans le **navigateur local** ;
- en cas de **zéro candidat bibliographique**, le navigateur local
  est immédiatement ouvert, avec texte « Aucune suggestion fiable ».
  L'absence de rapprochement ne bloque plus l'exploration ;
- une **liste de 25 EPUB/PDF par page** permet de sélectionner
  directement un fichier, avec recherche locale instantanée dans
  les métadonnées OPF titre/auteurs, le nom/chemin privé,
  les identifiants bibliographiques explicites et le format.
  Les flèches continuent de naviguer, et la liste est cohérente ;
- panneau « Filtres fichiers · format, SHA, CRC, tri » repliable :
  **EPUB/PDF**, SHA calculé/non calculé, CRC EPUB confirmé/autre,
  tri titre/auteur, nom ou ordre initial ; combinaison AND.
  Les fichiers avec SHA non calculé ne sont pas masqués par défaut,
  ni déclarés vérifiés. La recherche ne se limite pas aux 50
  premières entrées ;
- le fichier choisi affiche son détail privé, en parallèle avec
  le droit et ses causes de non-rattachement. Ce choix ne modifie
  pas les `metadata_only` déjà détectés, la provenance de
  fournisseur ou le statut des droits.

L'API privée `LocalInspection.unconfirmed_files` ajoute seulement
des **facettes gardées dans la session revocable** : format, existence
du SHA, CRC EPUB, texte de recherche limité et nom de fichier.
Aucun réseau, re-scan SQLite/fichiers ni export; les facettes et
recherches sont supprimées quand l'identité est révoquée. Les
renseignements viennent uniquement des racines inspectées, même
si le scan est incomplet.

## Limites de sécurité

La sélection manuelle est une **intention de comparaison**, pas une
preuve de droit, ni la preuve d'acquisition par le fournisseur.
Une concordance ISBN, nom, titre, taille ou octets SHA ne suffit
pas à garantir `confirmed_link`. **Aucun bouton de rattachement
irréversible** n'est créé : le préflight transactionnel avec preuve
d'identité/compte/entitlement/ledger/acquisition/placement,
nouvelle validation SHA/CRC et confirmation explicite reste un
lot distinct bloqué tant que l'inventaire réel n'est pas complet.
La recette utilisateur doit vérifier la recherche parmi les 200
fichiers, les 3 droits Numilog non vérifiés et le résultat d'un
éventuel scan complet, sans imposer un appariement 1:1.

Tests synthétiques : 6 EPUB dont 3 liens confirmés, SHA absent,
PDF, 120 EPUB supplémentaires, pagination 25, filtre forme/CRC,
source et identité conservées, validation d'arguments et preuve
de lecture seule. L'interface fonctionne avec la fenêtre
agrandissable de #2723 et l'onglet « Résultats » amélioré par
#2724/#2729.
