# Matérialisation confirmée d'une source (#1346)

> **Document historique/partiel — 28/10/2026.** matérialisation explicitement confirmée et spécialisée, pas le moteur A→B cible. La cible normative est la **synchronisation automatique unique** décrite dans [ARCHITECTURE.md](ARCHITECTURE.md) et [#3171](https://github.com/pzia/cifereca/issues/3171) ; le rattrapage manuel #2797 reste exceptionnel. Conserver les gardes existants ; ne pas déduire qu'un OriginLink canonique/transaction Core est livré de cette seule recette.

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
Les livres locaux non liés et sans correspondance exacte sont seulement signalés :
ils ne bloquent plus les nouvelles acquisitions. Un fichier candidat, un lien exact
ambigu ou une preuve physique invalide demeurent des motifs de rapprochement.
Aucune autorisation implicite de réessayer les refus de délégation.

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
Le consentement de sauvegarde au démarrage est décrit dans
[CORE-P0-1346-SOURCE-REFRESH-POLICY.md](CORE-P0-1346-SOURCE-REFRESH-POLICY.md).

## Fichier Numilog publié sans reçu (#1905)

Avant un nouveau téléchargement Numilog, sous le verrou d'acquisition, le Core
inspecte uniquement le répertoire Numilog de l'identité propriétaire. Un EPUB,
PDF ou KEPUB sans placement source attesté ou reçu de livraison valide bloque
l'appel fournisseur, y compris avec un nom lisible ou dans un sous-dossier ADEPT.
Le nom du fichier ne sert jamais à l'associer à un achat. Aucun fichier n'est
lu, adopté, déplacé ou supprimé par ce garde ; les autres comptes de la même
identité peuvent conserver leurs publications connues dans ce répertoire partagé.

L'inspection utilise des descripteurs de répertoires sans suivi de symlinks,
y compris dans les ancêtres de la racine. Elle est bornée à 4 096 entrées,
16 niveaux et 4 096 lignes de provenance par table ; erreur ou dépassement
refuse l'acquisition avec un message fixe, sans chemin ni donnée fournisseur.
Elle ne s'exécute ni au rendu des lignes ni pendant une recherche/actualisation
d'inventaire. Elle protège aussi les appels `on_start` déjà explicitement consentis.
Les droits et la connexion sont rejoués après l'inspection, juste avant HTTP.

Après un crash avant le reçu, le fichier reste à rapprocher et un nouvel essai
ne retélécharge pas silencieusement le livre. Ce garde ne fournit pas la preuve
d'édition nécessaire à une adoption automatique : une publication sans reçu ni
origine broker attestée reste bloquée, jamais associée par nom ou titre. Les
fichiers partiels non lisibles et les modifications externes après inspection
restent hors preuve.

Pour le cas distinct d'un fichier déjà indexé par le broker ACSM Core, #2117
permet une adoption atomique vers l'achat exact seulement si le registre
`manual-acsm`, l'origine canonique, le reçu privé, le propriétaire et les octets
concordent. Un ACSM autonome n'est pas une preuve de possession Numilog.
Deux rescans conservent la provenance ; une reprise avant/après adoption ne
refait aucun GET. Cette exception attestée n'adopte pas un fichier orphelin
arbitraire et ne crée aucun consentement automatique.
