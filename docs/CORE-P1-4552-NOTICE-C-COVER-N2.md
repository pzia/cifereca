# Notice C → miniature N2 : projection source exacte (P1 #4552/#2385/#6168)

## Contrat livré dans cette tranche

- Le champ de couverture appartient à **la notice source C**. `project_notice_cover_attribute` prend une `NoticeSourceObservation` admise et rejoue intégralement son admission (clé, JSON canonique, parseur et `source_revision`) avant de lire un **chemin de champ explicite** déclaré par le producteur.
- La fonction produit un `NoticeCoverAttribute` immuable : `DocumentRef` source/édition exacte, provenance `NoticeSourceKey`, révision du contenu/parser, état `available|pending|missing` et candidats de type `catalog`. Aucun URL n'est inféré par recherche heuristique, titre, ISBN, image ou SHA.
- Pour une source `public`, le `DocumentRef` canonique **n'inclut aucun profil spectateur**. Pour une source `private`, il conserve la provenance réelle de l'identité, du credential et du compte ; cela ne cloisonne **jamais les octets N3**. L'URL identique de deux éditions ne les fusionne pas.
- Seules les URL HTTPS qualifiées par la politique existante `core.covers.candidate` sont publiables. Les champs absents ne valent `missing` que si **l'observation ET l'inventaire du champ de couverture sont explicitement complets**. Une valeur non admissible produit `pending`, pas une preuve d'absence. Plusieurs candidats incompatibles restent `ambiguous` à la résolution N1 existante.
- `NoticeCoverN2Adapter.register` dérive le `CoverSourceSnapshot` N2 **du même attribut Notice C**, en projetant les changements documentaires sur une génération CAS strictement croissante. N2 ne persiste ni autre vérité documentaire ni corps de notice. La même révision repart en HIT ; `A→B→A` incrémente la génération et refuse l'ancienne publication.
- L'UI continue de demander `DocumentCoverService.get_cover(DocumentRef, variant)`. Aucune nouvelle règle de rendu, sélection, droit d'accès ou téléchargement n'est ajoutée. N3 reste l'unique cache matériel commun (URL/EPUB/previews).

## Monotonie des observations et protection de la dernière preuve complète

L'adaptateur conserve la dernière couverture documentaire **complète** quand une nouvelle observation du même `DocumentRef` est `partial`, a un champ de couverture malformé, ou déclare un inventaire incomplet : cet événement reste *inconnu*, jamais une suppression. Aucun nouveau token N2 ni notification GTK n'est émis pour ce retour à `pending`. Le watermark `observed_at` avance néanmoins, empêchant un résultat antérieur, arrivé en retard, de révoquer la décision courante. Une nouvelle observation complète plus récente peut établir une URL différente ou une véritable absence `missing` et invalide alors la décision N2.

`observed_at` n'est pas une révision transactionnelle C : à horodatage égal, l'adaptateur accepte une nouvelle preuve. La persistance Notice C devra assurer elle-même l'ordre CAS strict des publications et la révocation explicite des droits. Ce mécanisme ne conserve aucun corps de notice supplémentaire et ne touche jamais les octets N3.

## Frontières de preuve et dépendances

Cette première tranche source-catalogue est **volontairement inerte** : elle ne provoque aucun fetch, parsing, observation persistante ou admission automatique depuis la recherche. Le producteur Notice C/#3724 doit choisir un chemin de champ connu et déclarer si son inventaire est complet. La persistance/version des observations est le lot #2385. Une notice source `embedded` **ne peut pas** dériver une preuve `Placement` du seul SHA d'Artifact : le chemin EPUB exact reste sous l'admission Core existante de #6168.

Les niveaux `origin` (holdings) et `embedded` (EPUB) continuent à exiger leurs attestations propres, puis la consolidation Notice C #4552 devra décider entre candidats liés par une relation **forte**, avec la priorité `embedded > origin > catalog`. **Le présent adaptateur ne remplace pas le consolidateur C et ne se branche pas encore automatiquement sur les six vues**. Tant que le writer et les projections Notice C #2385 ne sont pas livrés, les bridges source existants de #6168 restent transitoires.

## Tests

`tests/test_core_p1_4552_notice_cover_attribute_n2.py` : partage de N2 public entre profils ; preuves privées distinctes et N3 commun ; mêmes URL / éditions distinctes ; `pending` sur couverture partielle ou champ malformé ; `missing` seulement complet ; conflit de candidats ; URL signées, locales ou privées refusées ; parser/revision, invalidation N2 et callbacks obsolètes ; refus de DTO altéré et d'EPUB sans Placement. CI, E2E GTK et AST restent à vérifier au HEAD exact avant fusion.

## Étapes d'intégration suivantes

1. Brancher l'attribut de couverture et son inventaire depuis les **producteurs autorisés** #3724 vers la persistance Notice C #2385, sans dédoubler le stockage.
2. Remplacer progressivement les `CoverSourceSnapshot` historiques de Recherche, Bibliothèque, Emprunts, Historique, Recommandations et Récents par la **projection C consolidée** sur le `DocumentRef` exact ; garder une adaptation transitoire tant que la source n'existe pas dans C.
3. Vérifier la matrice GTK six vues, les invalidations ciblées et les compteurs N2/N3, sans réveiller le scheduler P2 #4982.
