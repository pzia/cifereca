# Matérialisation confirmée d'une source (#1346)

Le broker public `sync` distingue trois opérations. `synchronize_source` conserve
son contrat inventaire/droits : il ne télécharge aucun livre. L'aperçu existant
`preview_local_materialization` reste consultatif et ne crée aucune permission.
Le nouveau parcours explicite, à exécuter dans une tâche hors du thread GTK, est :

```python
prepared = context.broker("sync").prepare_local_materialization(source_id)
# Montrer le compte/identité de SyncSourceRef et prepared.facts["counts"].
# Un clic explicite distinct est nécessaire avant l'appel suivant.
result = context.broker("sync").materialize_source(
    source_id, prepared.facts["confirmation_token"], control=task_control,
)
```

Ne jamais confirmer automatiquement à la suite de la préparation. Aucun appel
de préparation ou d'exécution n'est ajouté à l'ouverture d'une vue, au refresh
d'inventaire ou à un ordonnanceur par cette livraison.

La préparation lit uniquement le manifeste frais du compte déjà connecté et les
preuves locales ; elle ne résout aucun descripteur d'acquisition, ne contacte pas
le runtime ADEPT, ne scanne/répare pas le catalogue et ne modifie pas les données
métier. Elle prend le verrou d'acquisition commun pour sérialiser les observations
avec les acquisitions coopératives. Son fichier de verrou peut être créé.

La confirmation opaque est propre à l'instance du broker, à usage unique et
valable dix minutes. Au plus seize plans sont conservés en mémoire. Elle lie
l'identité, le fournisseur, le credential, le compte, le manifeste entier avec
ses révisions, les racines/configuration, les classifications par produit, les
preuves SQLite et les empreintes des fichiers déjà liés. Les sorties publiques
ne contiennent ni titre, chemin, produit, credential ni message fournisseur.
Le token doit rester dans la tâche locale ; ne pas le journaliser.

Lors de la confirmation, le broker reprend le verrou commun, revalide la source
activée/connectée, relit son manifeste et les preuves locales, puis refuse toute
différence avant une écriture métier ou acquisition. Le manifeste validé est
réutilisé par l'exécuteur : aucun nouveau manifeste ne peut élargir la portée.
Après normalisation fournisseur, le produit et sa révision doivent encore
correspondre à cette ligne précise du manifeste, avec le même fournisseur,
credential et droit possédé. Même un autre livre approuvé ne peut s'y substituer.
La source est revalidée à cette frontière avant toute résolution d'acquisition.
Un credential partagé entre comptes, un compte absent/ambigu, une migration de
dossier en attente, des symlinks ou des preuves incomplètes bloquent le parcours.
Le bypass historique `allow_unlinked_acquisition` n'est jamais hérité : des livres
non liés nécessitent d'abord une réconciliation. Aucune autorisation implicite
de réessayer les refus de délégation.

L'adaptateur transitoire Kobo réutilise sa politique et son exécuteur existants,
y compris les gardes d'idempotence, le ledger, les révisions et le runtime DRM.
Le chemin confirmé ne lance ni scan global, ni récupération de résidus, ni purge
autoritaire des anciens droits/backups. Aucun autre compte n'est interrogé ou
acquis. Les autres fournisseurs répondent `not_supported`.

Le contrôle de tâche utilise `TaskControl.cancelled()` et `report()` avec un
message fixe expurgé. L'annulation est coopérative **entre** les livres ; le livre
en cours termine son écriture et son checkpoint. La source/configuration est
revérifiée entre les éléments et avant chaque nouvelle acquisition.

`SyncResult.changed` compte les nouvelles acquisitions durablement attestées.
Après incident tardif, les lignes complètes sont relues pour éviter un faux zéro
si le fichier a été publié avant un échec de checkpoint. `completion_unknown`
signale un incident où un bilan exhaustif ne peut être garanti. Les fichiers
déjà acquis sont conservés. Une nouvelle préparation est nécessaire après un
refus, annulation, reprise ou redémarrage ; aucun token n'est repris sur disque.
Les cas de fichier résiduel non attesté restent bloqués par les gardes existantes.

Le checkpoint Kobo existant reste l'unique checkpoint par élément. La projection
générique de phase conserve séparément dernier essai et dernier succès de
matérialisation ; un résultat partiel/bloqué/annulé ne remplace ni ce succès ni
l'horloge d'inventaire. Une phase `running` d'un processus précédent est présentée
comme `interrupted`, sans fabriquer un succès. Ces états ne sont pas des permissions.

Ce lot fournit le contrat d'exécution Core. La planification/migration par
source/phase, l'extraction progressive de politique fournisseur et le parcours
de confirmation CoreUI #1347 restent à livrer ; le parent #1346 reste ouvert.
Les tests offline ne qualifient pas les comptes ni les runtimes externes réels.

## Reçu de livraison Numilog (#1905)

Une acquisition Numilog manuelle validée conserve désormais un reçu durable
avant de rendre le fichier à l'appelant. Le reçu lie identité, connexion, compte,
achat, racine, chemin, taille et SHA-256. Il bloque une seconde acquisition du
même achat pendant l'indexation et reste `attention_required` si celle-ci échoue.
Le passage à `complete` vérifie atomiquement le reçu unique, l'artefact et son
placement exact dans SQLite.

`service.resume_numilog_materialization(acquisition_id)` reprend explicitement
l'indexation des mêmes octets, sans HTTP ni session fournisseur, y compris après
redémarrage. L'identité sélectionnée et le propriétaire persistant de la connexion
doivent toujours correspondre. Un changement de racine, fichier, propriétaire,
un symlink ou un ledger ambigu bloque cette reprise. L'annulation conserve le
reçu sans fabriquer un succès. Aucun scan global ni récupération automatique
n'est déclenché. La fiche Bibliothèque/recherche locale propose
« Reprendre l’indexation Numilog » pour un reçu en attente lié à son unique
possession. L'affichage ne lit pas le fichier ; le clic explicite le revalide
dans une tâche hors GTK, sous verrou, avant l'indexation. Un bouton devenu
obsolète ne peut indexer un autre achat ou un reçu modifié.

Ce reçu couvre la fenêtre livraison validée → indexation. Il ne qualifie pas
un crash pendant le téléchargement ou avant l'écriture du reçu, et ne constitue
ni consentement à une sauvegarde automatique ni preuve fournisseur réelle.
Le parent #1905 reste ouvert.
