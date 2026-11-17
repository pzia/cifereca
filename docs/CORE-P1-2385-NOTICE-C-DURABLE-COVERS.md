# P1 #2385 / #4552 / #6168 — Notice C persistant et miniatures dérivées

## Source de vérité et cache

Une **NoticeSourceObservation** C déjà admise est la seule autorité documentaire. Sa clé contient la source, le provider, la visibilité, l'édition, le parser et la provenance du propriétaire réel. Le champ **cover** est projeté depuis les champs descriptifs *du même record* et le chemin déclaré par le parser, jamais depuis un rapprochement par titre, ISBN, image ou SHA. Les URL ne valent pas preuve d'édition ou de droit.

Le dépôt Core **notice_cover_repository** range cette notice (avec son contrat de champ, son état complete/partial et son epoch monotone) dans l'index primaire SQLite **metadata** sous une clé opaque par DocumentRef exacte. Il n'y a pas de table bibliographique parallèle réservée aux miniatures, ni de copie des octets image, ni de partition par **profil spectateur**. Le public est partagé sur l'installation ; une observation privée conserve son véritable owner/credential/account, sans transférer de droit d'action.

Le lecteur Core **read_notice_cover** reconstruit et revalide la notice, sa révision et la DocumentRef à chaque lecture. Une ligne absente vaut **inconnu**, non une couverture officiellement absente. Une ligne corrompue est une erreur de provenance, jamais une négative.

## Contrat d'écriture des producteurs Core

Un adaptateur réellement autorisé par sa source/parser appelle :

~~~python
persist_notice_cover(
    storage, admitted_observation,
    field_path=("media", "cover"),
    cover_inventory_complete=True,
    expected_epoch=current_epoch,
)
~~~

Le port rejoue la validation Notice C et produit un nouvel epoch N2 seulement lorsque les faits/leur contrat changent. Un refresh partiel ne peut effacer une notice complète ; les mises à jour périmées sont refusées. Des modifications dans la même seconde exigent un CAS exact. Les plugins ne doivent pas créer artificiellement la preuve à partir d'un DTO GTK, thumbnail URL, cache média, ISBN ou chemin EPUB deviné. Aucune acquisition, session fournisseur, mutation de prêt, GET ou scan EPUB n'est déclenché ici.

## Lecture N2 et affichage

1. Le widget visible connaît une **DocumentRef exacte** et appelle le cache commun N2 en liste ou détail.
2. Sur N2 MISS, le worker borné lit la notice C persistée par la passerelle **cached_notice_cover_snapshot**. Ni GTK ni le provider ne font d'I/O à ce stade.
3. Une preuve trouvée est projetée dans N2, puis les abonnés GTK encore actifs rebindent la cellule (contrat #6325). Un manque ou une corruption n'invente pas une sélection.
4. Après sélection N2, seul N3 accède à l'image URL, au membre EPUB ou à une miniature HiDPI. N3 reste mutualisé sur l'installation.
5. Le producteur d'une notice modifiée est responsable de la notification aux projections N2 actives. Une écriture SQLite isolée n'implique pas à elle seule une diffusion cross-process.

## Portée de la livraison

Le stockage Core exact, la relance après redémarrage, l'index par source et la lecture N2 effective sont livrés. La première intégration ne branche **pas encore** tous les producteurs #3724 sur ce writer, ni les six vues sur des notices C réelles, ni une souscription SQLite interprocessus. Les tickets #3104 et #6168 restent ouverts pour réception.

Les tests doivent maintenir : public A→B→A, comptes privés distincts, deux éditions avec URL commune, partielles et fausses négatives, provenance ancienne/révision CAS, lecture après redémarrage, callbacks périmés, EPUB prioritaire et absence de nouvelles permissions d'action.


## Éviction et générations d’écriture (#4982)

Le writer conserve une unique fence `notice.c.source-fence.v1:` dans les
metadata SQLite : epoch, watermark et digest de la preuve, aucun corps de
notice ni URL. Le port `evict_notice_cover(..., expected_epoch=...)` avance
atomiquement cet epoch et inscrit `evicted: true`, puis retire uniquement
l'observation. `read_notice_cover_write_epoch` retourne l'epoch d'écriture
(live ou éviction qualifiée), distinct d'une preuve documentaire et jamais
utilisable comme révision d'image. Les anciens CAS sont refusés même après
restart ; seule une observation autorisée utilisant la nouvelle génération
peut renouveler à la même seconde. Les batchs non-CAS restent soumis au
watermark, et un échec SQL annule ensemble retrait et fence.

Une suppression SQL directe post-déploiement est protégée par la dernière
fence active : aucun CAS de réhydratation n'est exposé ; seule une observation
indépendamment admise sans CAS conforme au watermark peut recréer la source
avec un epoch supérieur. Les sources supprimées avant déploiement, sans
marqueur ni observation, ne permettent pas de reconstruire un historique
perdu. Les fences ne sont pas des caches, leur rétention est durable.
La C consolidée, N3, les droits et les fichiers restent inchangés.
Pas de TTL automatique, de scheduler ou de nouveau GET. Coût metadata
supplémentaire par source à mesurer. La protection du dernier état complet
après éviction reste une tranche distincte.
