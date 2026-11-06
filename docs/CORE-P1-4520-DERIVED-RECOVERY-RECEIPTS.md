# P1 #4520 — reprise FS→SQLite d'une variante locale (tranche Core interne)

## Protocole de publication

Une variante ne devient autoritative qu'après publication physique par `os.link` exclusif **et** transaction SQLite contenant `Artifact`, `Placement`, `OriginLink(derived)`, recette et notice. Aucune source, droit fournisseur ou entitlement n'est recopié.

1. Core vérifie le Placement source, le profil actif, ses octets et les stamps d'inode parent/sortie `(dev, ino, size, mtime_ns)`.
2. Avant **chaque tentative** de hardlink, Core crée dans `.cifereca-staging/variants` un reçu JSON borné à 8 KiB, en mode 0600, par `O_EXCL|O_NOFOLLOW` et répertoire épinglé par fd. Fichier résultat, reçu et répertoire sont `fsync` avant lien.
3. Core enregistre avant lien, sous `BEGIN IMMEDIATE`, un **intent SQLite** `metadata["derived.publication.pending.v1:<nonce>"]` contenant l'empreinte exacte du reçu créé. Un JSON falsifié/copied ne suffit pas : le reçu et son intent doivent concorder. L'intent n'est ni un OriginLink ni un droit.
4. Core crée le hardlink exclusif, contrôle l'inode/la SHA et `fsync` le répertoire destination avant la transaction autoritative.
5. **Après commit réussi, le reçu et son intent SQLite sont délibérément conservés. Aucun `unlink` automatique du reçu n'est exécuté**, même si les octets sont valides. Un appel de revue voit ce reçu comme `already_committed`. Si SQLite échoue/crashe, le reçu est conservé comme `recoverable` ou `manual_review`.

## Reprise supervisée

`review_pending_derived_receipts(service, identity_id=..., commit=False)` est une API Core interne **non exposée** à un plugin ni déclenchée au démarrage.

- Lecture seule par défaut. Scan récursif du profil et de ses sous-dossiers ordinaires (via descripteurs ancrés au profil et `O_NOFOLLOW` **sur chaque composant** (symlinks de répertoires exclus, profondeur maximale 32, au plus 8 192 dossiers visités ; dépassement ⇒ refus explicite, non destructif)) ; au maximum **128 reçus par page** (8 KiB chacun). Chaque entrée expose un `cursor` relatif au profil ; si une page suivante existe, la dernière expose `next_cursor`. Utiliser `after_cursor=<next_cursor>` pour continuer. Les résultats sont triés lexicalement et la mémoire de sélection est bornée à 129 entrées, sans supprimer aucun fichier. L'itération n'est pas un snapshot atomique si un acteur modifie les répertoires simultanément. Les sous-arborescences jusqu'à 32 niveaux sont incluses ; au-delà, aucune promotion n'est autorisée sans revue de limite.
- L'opérateur doit valider l'empreinte précise du reçu et fournir `approved_receipt_sha256` pour `commit=True`, avec identité actuellement active identique.
- Vérification de l'intent SQLite, du placement source exact, du chemin canonique, des stamps inode et SHA source/sortie via fd `O_NOFOLLOW`. Sous le verrou `BEGIN IMMEDIATE` du constructeur `publish_derived_variant`, revalidation de l'intent **et** nouveau hash des deux inodes avant création de `OriginLink(derived)`.
- Un parent supprimé et recréé avec les mêmes octets mais un nouvel inode est refusé. Un reçu manquant/altéré, symlink, changement de profil, collision, retrait d'intent ou changement physique mène à `manual_review`, jamais à une purge.
- `already_committed` exige le Placement dérivé et son `OriginLink(derived)` exact ; aucune attribution d'origine par la seule SHA.

## Décision de sûreté sur le nettoyage

POSIX ne garantit pas un `unlink` atomiquement conditionné au numéro d'inode. Dans un dossier pouvant être modifié par un acteur concurrent, un contrôle `fstat` juste avant `unlink` ne suffit pas. **Cette tranche supprime entièrement les chemins de nettoyage automatique des reçus**, au lieu de supprimer potentiellement un fichier substitué.

**Coût et limite connus :** les reçus et intents s'accumulent également après succès. **La consultation est paginée et bornée à 128 entrées par appel**, y compris au-delà de 128 reçus persistants, mais l'occupation disque et le coût de l'inventaire continuent de croître. Une politique ultérieure de journal privé Core / consolidation **non destructive** et les modalités de supervision et les limites de parcours des très grandes arborescences doivent être qualifiées avant activation production. Aucune purge silencieuse, aucun quota de stockage implicitement garanti.

## Tests / statut

Tests FS et SQLite synthétiques : succès normal avec reçu `already_committed`, crash injecté avant OriginLink, fermeture et réouverture de SQLite, reprise supervisée idempotente, refus sans approbation, autre profil, source/sortie remplacées à la frontière transactionnelle, source même SHA mais inode distinct, reçu falsifié, symlink et remplacement concurrent de reçu (octets identiques ou différents). Aucune source réseau ni bibliothèque utilisateur.

**À solder avant fusion :** limite de 128 reçus, parcours supervisé, très grandes arborescences (plus de 8 192 dossiers ou profondeur >32) et coûts en fsync/hachage. Aucun hook startup, aucune suppression de fichier orphelin, aucun droit fournisseur nouveau. #4520 reste P1.

### Protection contre la substitution de chemins intermédiaires

Le parcours récursif ouvre les répertoires sous un fd du profil par chaîne `openat(O_DIRECTORY|O_NOFOLLOW)`, puis énumère par `scandir(fd)`. La lecture des reçus et le re-hachage du parent et de la sortie utilisent la même discipline, y compris sous `BEGIN IMMEDIATE` lors d'une reprise approuvée : un lien symbolique injecté dans un répertoire **intermédiaire** ne doit jamais rediriger une lecture vers un autre profil. Tests de chemin sortant du profil et de symlink parent. Ce durcissement n'autorise toujours pas de récupération au démarrage ni d'exposition plugin.
