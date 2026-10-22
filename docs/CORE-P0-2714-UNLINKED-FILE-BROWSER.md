# P0 #2714 — retrouver un EPUB non lié sans suggestions bibliographiques

## Problème terrain

Sur l'installation réelle, l'audit partiel signalait **200 fichiers
sans lien confirmé** et **408 droits sans fichier vérifié**, mais la
liste de candidats par édition était vide. Le précédent écran
« Comparer tous les fichiers non liés » affichait **un seul fichier
par flèche**, sans filtres structurés ni choix direct : impossible de
retrouver efficacement un EPUB parmi les centaines de fichiers.
Une suggestion ISBN/SHA absente ne prouve pas qu'un livre est absent.

## Navigation — uniquement privée / lecture seule

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
pas à garantir `confirmed_link`. Le bouton de vérification
**ne rattache aucun fichier**. Seule l'opération Numilog explicite,
lorsque toutes les preuves sont présentes, peut proposer une
confirmation supplémentaire ; une analyse incomplète ne permet
pas de rendre le placement éligible.
La recette utilisateur doit vérifier la recherche parmi les 200
fichiers, les 3 droits Numilog non vérifiés et le résultat d'un
éventuel scan complet, sans imposer un appariement 1:1.

Tests synthétiques : 6 EPUB dont 3 liens confirmés, SHA absent,
PDF, 120 EPUB supplémentaires, pagination 25, filtre forme/CRC,
source et identité conservées, validation d'arguments et preuve
de lecture seule. L'interface fonctionne avec la fenêtre
agrandissable de #2723 et l'onglet « Résultats » amélioré par
#2724/#2729.


## Placement Numilog explicite, double confirmation (#2714-C3)

Ce parcours ne s'applique qu'à un **droit Numilog `owned/active`**
associé de façon unique à l'identité, au compte et à l'édition courants,
et à un EPUB local dont les octets SHA/CRC et le reçu d'acquisition
complet concordent. Aucun résultat bibliographique ne vaut preuve.

1. Le bouton **« Préparer le rattachement »** ne fait que lire
   l'inspection privée ; il affiche ce qui manque et n'écrit rien.
2. **« Vérifier les preuves actuelles… »** relit, dans une tâche de
   fond, les preuves SQLite et les octets locaux sans suivre de lien
   symbolique ; un jeton de prévisualisation demeure exclusivement
   dans la session Core et est révoqué quand la sélection change.
3. **« Confirmer le placement local… »** n'est visible qu'après
   un contrôle Core réussi. Il ouvre un *nouveau* dialogue avec
   **« Annuler »** comme réponse par défaut. Seul un clic explicite
   sur **« Confirmer le placement »** lance l'opération SQLite sous
   verrou et revalidation. Les données source, droits et octets
   du fichier ne sont pas modifiés.
4. Une réussite retire immédiatement la vue d'inspection devenue
   potentiellement périmée et demande une nouvelle réconciliation.
   Refus, annulation ou changement d'identité/sélection n'autorisent
   aucune écriture.

Le droit et le reçu existants ne sont jamais créés à partir d'un titre,
d'un ISBN ou d'une URL publique. **L'interface n'accepte pas de jeton
de transaction en entrée** ; elle ne présente ni token, ni compte,
ni SHA dans les données Diagnostics. L'undo transactionnel est un
lot distinct et n'est pas exposé par ce bouton.
