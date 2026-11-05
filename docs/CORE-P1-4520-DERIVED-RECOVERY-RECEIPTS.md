# P1 #4520 — reprise FS→SQLite des variantes dérivées (tranche supervisée)

## Invariant et menace

Un fichier dérivé est publié par lien physique exclusif avant l'enregistrement atomique de `Artifact + Placement + OriginLink(derived) + recette + notice`. Un arrêt à cette frontière laisse un inode réel sans autorité SQLite. Un SHA ou un nom de fichier identique **ne prouve pas** la causalité, l'identité ni les droits. Aucune reprise non supervisée n'est autorisée.

## Protocole d'écriture

1. Core valide le Placement source actif et exact, son identité, ses octets et le résultat transformer.
2. Avant chaque tentative de lien `os.link`, Core crée sous `.cifereca-staging/variants/pending-<aléatoire>.json` un reçu de taille bornée (`0600`, `O_EXCL|O_NOFOLLOW`) portant source/Placement/profil exact, recette/versions, empreintes, chemin cible et signature inode `(dev, ino, size, mtime_ns)`. Le résultat, le fichier JSON et son répertoire sont `fsync` **avant** le lien.
2 bis. Core enregistre sous verrou SQLite un **intent** `metadata` avec clé aléatoire `derived.publication.pending.v1:<nonce>` et empreinte SHA-256 des octets du reçu. Le reçu sur disque seul ne peut jamais valider une nouvelle provenance : le hash doit également être inscrit dans la base **avant le hardlink**. Ce marqueur est un checkpoint technique, **pas** un `OriginLink`, un Placement ou un droit.
3. Le lien destination est créé exclusivement et revérifié ; le répertoire de publication est `fsync` **avant** la transaction SQLite. Le répertoire staging est synchronisé après retrait du fichier temporaire.
4. Après succès de la transaction SQLite, le reçu est nettoyé au mieux, puis son intent en base est retiré si le fichier a bien disparu ; si l'arrêt intervient après commit, il peut rester sur disque et est reconnu `already_committed` sur réinspection. Une erreur SQLite laisse le reçu pour examen : **ni purge de fichiers ni suppression du reçu**.

## Reprise explicite

API Core interne `review_pending_derived_receipts(service, identity_id=..., commit=False)`. Le mode sans écriture est le défaut et renvoie `recoverable`, `already_committed` ou `manual_review` avec le digest SHA-256 du reçu récupérable. Les scans portent uniquement sur le répertoire enregistré du profil et ses sous-dossiers directs non-symboliques, avec limite de 128 reçus et de 8 KiB par reçu. **Un sous-dossier plus profond n'est pas pris en charge dans cette tranche.**

Pour un commit, le superviseur doit fournir **le SHA-256 du reçu exact lu en prévisualisation** via `approved_receipt_sha256`. `commit=True` sans approbation explicite renvoie `approval_required`, jamais une publication. La procédure exige aussi que l'identité ciblée soit le profil **actuellement actif** pour tout `commit=True`. Elle revalide le répertoire, le parent `Placement` exact et actif, le profil, la signature inode, les hachages des deux fichiers avec ouverture `O_NOFOLLOW`, **la concordance du hash du reçu avec son intent SQLite**, et l'absence de revendication cible par SQLite. Elle réutilise uniquement le constructeur Core `publish_derived_variant`, qui **revalide l'intent et son empreinte pendant la transaction `BEGIN IMMEDIATE`** : une suppression de l'intent entre revue et commit échoue sans publier. Ce constructeur ne crée ni SourceReference ni entitlement fournisseur.

## Limites de sécurité

- Le reçu réside sur le disque du profil, accessible à son propriétaire. **Sa seule existence ne constitue pas une preuve infalsifiable de causalité ; un reçu modifié ou copié sans intent SQLite correspondant est rejeté.** L'approbation humaine du contenu, l'identité active et les coordonnées exactes sont obligatoires avant publication.
- Aucun appel automatique au démarrage : un parcours UX supervisé et la provenance autoritative de cette approbation restent des travaux séparés. Ne pas exposer `commit=True` via un plugin non privilégié.
- Les fichiers absents, modifiés, symboliques, chemins partagés, collisions, JSON invalides ou profils incompatibles restent en `manual_review`, jamais supprimés ni rattachés sur simple SHA.
- Le parent ou le fichier peut être remplacé par un processus concurrent entre la dernière vérification du lecteur et la transaction ; les tests sur courses adversariales restent requis avant d'activer un point d'entrée utilisateur.
- Le temps de `fsync` et le coût du hachage doivent être mesurés ; aucun gain temporel revendiqué.

## Critères de tests

SQLite + FS synthétiques réels : reçu forgé ou modifié sans intent SQL, intent supprimé entre contrôle et transaction, profil actif erroné, lecture de reçu symlink, taille de sortie falsifiée, conversion normale et nettoyage reçu, SQL interrompu avant OriginLink, reprise après redémarrage, idempotence, absence de droit fournisseur, refus d'une reprise sans approbation, profil étranger, octets modifiés, symlink cible. Sans réseau ni compte réel.

**Ne pas clore #4520 à ce stade :** reste à qualifier le parcours supervisé, les sous-arborescences profondes et les courses TOCTOU adversariales. Ne pas confondre cette reprise avec la migration des variantes historiques sans reçu.
