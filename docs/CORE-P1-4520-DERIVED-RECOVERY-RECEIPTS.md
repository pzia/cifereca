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


## Bilan de rétention en lecture seule (06/11/2026)

La fonction Core interne `audit_derived_receipt_retention(service, identity_id=..., after_cursor="")` expose un **inventaire paginé** (au plus 128 reçus par appel) avec les compteurs de statuts `already_committed`, `recoverable`, `manual_review`, le nombre d'octets effectivement mesurés, le nombre de tailles non lisibles, le curseur suivant et `destructive_cleanup_allowed=False`. Elle réutilise la vérification de profil de la revue supervisée et lit les tailles par descripteurs `O_NOFOLLOW` à chaque composant, sans ouvrir les liens symboliques externes.

Cette API **ne constitue pas une consolidation** : elle permet d'estimer la pression sur l'espace disque et la part d'archives déjà attestées avant de choisir une rétention de long terme. Les compteurs sont **par page** et non un total atomique, et l'inventaire peut évoluer si le propriétaire modifie simultanément les dossiers. La taille en SQLite des intentions n'est pas incluse dans `receipt_bytes_observed`. Une entrée sans taille vérifiable augmente `unknown_size_count`, sans lire les octets d'un autre profil ni supprimer un fichier.

**Politique retenue pour cette tranche :** ne pas transformer `already_committed` en permission de purge automatique. Le stockage physique reste potentiellement croissant ; une migration vers un journal Core contrôlé et une consolidation explicitement approuvée sont des travaux ultérieurs, avec conservation des preuves de causalité, reprise idempotente et tests de crash. La PR reste draft et la fonctionnalité n'est pas exposée au backoffice.


## Compaction supervisée des intents terminés (06/11/2026)

Une fois la publication canonique terminée, la base détient déjà le `Placement` dérivé exact et son `OriginLink(derived)` ; l'intent `derived.publication.pending.v1:<nonce>` n'est alors plus nécessaire **pour créer** cette origine. L'API Core interne `compact_committed_derived_intent(service, identity_id=..., receipt_cursor=..., approved_receipt_sha256=..., after_cursor="")` permet à un superviseur de retirer **une seule ligne metadata SQLite**, sans modifier aucun fichier. Ce point d'entrée ne s'exécute jamais automatiquement.

Garanties : l'opérateur sélectionne un reçu par curseur exact et fournit son SHA approuvé ; le profil doit être actif. La revue initiale doit confirmer `already_committed` par validation physique du parent et de la sortie et par correspondance exacte du Placement et de l'OriginLink. **Dans la même transaction `BEGIN IMMEDIATE` que le `DELETE`**, la compaction revalide la présence de cet OriginLink canonique et l'empreinte du pending intent. Si la ligne est déjà absente mais l'origine confirmée, le résultat est `already_compacted` (idempotence). En cas de reçu orphelin, origine manquante, hash non approuvé, autre profil ou intent modifié : refus sans mutation. Aucun `unlink` ni modification de droit fournisseur.

La revue reconnaît toujours `already_committed` **même après disparition de l'intent**, mais uniquement lorsque l'origine dérivée et le Placement exacts existent déjà. Un reçu orphelin **sans** intent reste `manual_review` et ne peut jamais être promu par SHA. La conservation des reçus disque demeure intégrale : ce protocole compacte uniquement la croissance des lignes d'intent SQLite, pas l'espace disque. Les tests couvrent idempotence, conservation du reçu/origine, refus sur publication interrompue, identité inactive et absence de preuve canonique.

Cette API doit rester Core interne / draft jusqu'à définition du parcours superviseur et d'une politique explicite de rétention physique.


## Synthèse de rétention globale bornée

La fonction Core interne `summarize_derived_receipt_retention(service, identity_id=..., after_cursor="", max_pages=16)` agrège les résultats de l'inventaire sur **1 à 64 pages** de 128 reçus maximum, avec un budget par défaut de 16 pages. Elle retourne le nombre de pages et de reçus, les statuts, les octets mesurés, les tailles indéterminées, `complete` et un `next_cursor` permettant de reprendre lorsque le budget est épuisé. Un curseur qui ne progresse plus est refusé. L'inventaire est **non atomique** en présence de modifications concurrentes et ne constitue pas une preuve d'intégrité à un instant donné.

Cette synthèse n'effectue aucune mutation, ne retire aucun reçu et expose toujours `destructive_cleanup_allowed=False`. Elle permet de quantifier la croissance des preuves physiques et de préparer un budget de rétention, mais ne compacte pas les fichiers. Le plan de stockage Core protégé reste un préalable à la mise en service.


### Réattestation au moment du retrait SQL

La suppression supervisée de l'intent relit et rehache le **reçu original une seconde fois sous `BEGIN IMMEDIATE`**. Une modification survenue entre l'examen de l'opérateur et le DELETE est refusée, même si la preuve de publication canonique existe ; le marqueur est conservé. Un test injecte cette altération à la frontière transactionnelle et vérifie l'absence de compaction.


## Soupape de capacité locale non destructive (06/11/2026)

Tant qu'une zone de reçus sous contrôle exclusif du Core et sa consolidation sûre ne sont pas disponibles, l'écrivain borne **localement** le nombre de noms `pending-*.json` dans chaque répertoire de staging à `MAX_RECEIPTS_PER_STAGING_DIRECTORY = 1024`. Il énumère les entrées via le descripteur du staging déjà épinglé. Si la capacité est atteinte, la tentative échoue **avant** création d'un nouveau reçu, d'un intent SQLite ou d'un lien physique de publication avec le conflit Core typé `artifact_variant_receipt_capacity_exceeded` (garde interne `derived_receipt_retention_capacity_exceeded`). Aucun reçu existant n'est supprimé, modifié ni réattribué ; des tests couvrent le dernier emplacement libre et le blocage à capacité.

**Limites :** cette soupape est volontairement *fail-closed* et **par répertoire**. Elle n'impose pas de quota global en présence de multiples dossiers livres ; la simple énumération n'est pas un mécanisme atomique de réservation entre plusieurs processus concurrents. Elle ne constitue donc pas une solution de rétention physique complète. À capacité, un opérateur doit résoudre la politique de stockage/consolidation Core sûre ; ne pas augmenter silencieusement le seuil ni purger des fichiers. Le statut draft reste nécessaire.
