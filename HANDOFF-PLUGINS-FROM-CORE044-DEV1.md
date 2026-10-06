# Handoff Plugins — Core044-dev.1

Core044 ne demande **aucune migration plugin**.

- Core : `0.9.44+dev.1`
- Plugin API : `1.4.0`
- Manifest : `1`
- contrats publics : inchangés par rapport à Core043

Les optimisations sont entièrement Core-owned : SQLite/batch, coordination de recherche et diff de liste GTK. Les plugins continuent d'utiliser les mêmes brokers, catégories, capabilities et modèles publics.


## État après la vague plugins du 6 octobre 2026

La vague d’évolutions acceptées est terminée, testée et mergée dans `main`.

Livré et couvert en intégration :
- Ebooks libres et gratuits : filtres auteur/catégorie/genre, harness local et téléchargement EPUB.
- BnF Catalogue général : séries/collections et pseudo-SRU d’intégration.
- Gallica : recherche/téléchargement PDF en plus des EPUB.
- Project Gutenberg : navigation par langue et bookshelf/thématique.
- Diagnostics : contrôles actifs via brokers publics, strictement en lecture seule.
- Accueil / Home : section « À traiter » fondée uniquement sur les données réellement exposées par Core.
- Maintenance Library Audit : audit d’intégrité fichiers, strictement en lecture seule.
- Numilog : parcours ACSM vers le broker d’acquisition Core et fulfillment ADEPT ; correction du suffixe temporaire `.acsm`.
- Kobo-Adept Runtime : cycle de vie des profils validé via vrai sous-processus JSON simulant le contrat CLI v8.

PR de cette vague : #15, #17, #18, #20, #22, #23, #24, #25, #26.  
La PR expérimentale multi-catalogues #13 a été fermée et ne doit pas être reprise.

## Évolutions plugins restantes

### Volontairement écartées pour l’instant
- **Baen** : parcours personnel « My Books » / authentification.
- **Kobo provider** : restauration d’un livre archivé.
- **Paris** : création de réservation.
- **EPUB Optimizer** : évolution fonctionnelle CSS/layout ; à traiter dans un fil dédié.

### Bloquées ou dépendantes du Core
- **Home — échéances/réservations** : le plugin sait consommer `due_soon_loans` et `available_bookings` si Core les expose, mais Core044 ne publie pas encore ces compteurs. Ne pas les fabriquer côté plugin.
- **Maintenance — réparation automatique** : l’audit est livré. Toute réparation doit rester Core-owned via un contrat/broker de mutation vérifiable ; ne pas ajouter d’écriture directe depuis le plugin.

### Qualification externe restante
- **Kobo-Adept Runtime** : la forme du contrat subprocess/JSON est testée dans Cifereca, mais une qualification contre le vrai binaire/dépôt `kobo-adept` reste souhaitable dès qu’il est accessible dans l’environnement de test. Ne pas confondre ce point avec un défaut fonctionnel actuellement reproduit.

## Règle pour les prochaines évolutions plugins

Conserver le travail **un plugin à la fois**, avec :
1. une branche dédiée ;
2. un test d’intégration utilisant autant que possible les brokers publics Core et le harness `gen-dataset` ;
3. packaging + suite plugins + smoke dataset lorsque pertinent ;
4. pas d’accès aux APIs privées Core ni de mutation directe lorsque le Core possède déjà le domaine.
