# P0 #4519 — publication Converter sans écrasement / refus de scope ambigu

Le Converter produit un **nouvel EPUB**, pas une modification de l'original.
Le SHA d'Artifact est global et ne désigne pas le propriétaire d'une copie.

## Garde Core livré

`ArtifactExecutionBrokerImpl` refuse les transformations quand un même
Artifact d'entrée possède plusieurs Placements `present`, **même au sein
d'un seul profil**, jusqu'à l'ajout ultérieur d'un identifiant de Placement
source autoritatif dans le contrat du transformer (#4520). Il refuse aussi
les chemins source absents, non absolus, symlinks et répertoires source
non réguliers. Il exige en outre la racine Core configurée par
`library.directory` et un répertoire `identities.directory_name` unique :
les octets hors du sous-arbre physique de cette identité sont refusés,
même lorsqu'il n'existe qu'un seul Placement SQLite. Le contexte du source (Placement exact, identité, inode du
fichier et du répertoire) est épinglé dans les deux leases, puis revalidé
avant publication. Les octets du source sont à nouveau hashés.

Le Placement source doit également appartenir au profil **actif** du
service (`current_identity.identity_id`). L'absence d'identité active
ou la demande de B pour un Artifact uniquement présent chez A entraîne
`artifact_source_identity_mismatch`, avant accès au fichier. Un changement
de profil pendant la transformation est recontrôlé avant le commit.

L'ancien cache de variantes est toujours une table par SHA mondial :
un cache sans Placement physique dans l'identité source sélectionnée
ne peut donc pas satisfaire la requête ; un cache dont le Placement
de sortie est ambigu n'est pas réutilisé non plus.

La publication n'appelle plus `os.replace` : elle utilise `os.link`
sur le fichier de staging régulier, en s'appuyant sur des descripteurs de
répertoires `O_DIRECTORY|O_NOFOLLOW`, avec cible **exclusive** et
`follow_symlinks=False`. Il n'existe que deux noms possibles :

1. `<nom-proposé>.<format>` ;
2. `<nom-proposé>-<SHA8>.<format>`.

Si les deux chemins sont déjà occupés physiquement, par symlink ou par
un Placement SQLite même lorsque le fichier manque, **refus explicite**
`artifact_variant_filename_conflict` : aucun écrasement. Une création
concurrente de destination échoue par `EEXIST`, sans remplacer la cible.
Les signatures des répertoires (y compris **les deux niveaux**
`.cifereca-staging` et `variants` épinglés séparément) et le SHA de
la sortie sont recontrôlés avant de retirer le staging
et d'enregistrer l'Artifact. L'annulation utilise un descripteur
`O_DIRECTORY|O_NOFOLLOW` du staging et vérifie son inode avant `unlink` :
une substitution du dossier par un symlink vers un autre profil ne peut pas
détourner ce nettoyage.

Une annulation signalée après la phase `transform` mais avant le commit
ne publie pas de nouveau fichier. Les références et fichiers existants ne
sont jamais modifiés pour résoudre un conflit de nom ou d'identité.

## Frontière avec #4520

Le P0 n'implémente pas le nouveau modèle `OriginLink derived`, la
transaction globale FS+SQLite de publication, ni un identifiant de Placement
contextuel dans `TransformRequest`. Le P1 doit notamment traiter les
annulations/crashes après publication physique mais au milieu des écritures
SQLite, ainsi que le scope exact et transactionnel du Placement B dans
`TransformRequest`. Les refus conservateurs du P0 sont souhaités tant
que ce contrat complet manque. Le gel de déduplication #4496 reste intact.

## Acceptation synthétique

Tests FS+SQLite réels : double collision (dont même SHA), symlinks,
cible de repli physiquement absente mais réservée par SQLite à un autre
profil, course `EEXIST`, source globale dans plusieurs profils ou deux
placements, cache étranger, changement d'octets de l'original, substitution
du répertoire source et du répertoire de staging (pendant l'échec **et**
pendant la publication), puis annulation après transformation.

Aucun livre utilisateur, fournisseur ou téléchargement n'est utilisé.

La substitution du répertoire parent de `variants` par un symlink vers
le dossier d'origine déplacé (inode `variants` identique) est également
refusée, en commit et en abort. Ce cas empêche de contourner la garde
`O_NOFOLLOW` appliquée au seul dernier composant.

La réservation du nom temporaire ne dépend plus d'une séquence
`mkstemp(chemin)` / `unlink(chemin)` redirigeable : elle s'effectue avec
`os.open(..., O_CREAT|O_EXCL|O_NOFOLLOW, dir_fd=stage_fd)`, puis
`unlink(..., dir_fd=stage_fd)` sur le **même répertoire épinglé**.
Le transformeur reçoit toujours un chemin temporaire libre, tandis que
le Core ne supprime jamais un fichier par un chemin susceptible de traverser
un staging remplacé par symlink.
