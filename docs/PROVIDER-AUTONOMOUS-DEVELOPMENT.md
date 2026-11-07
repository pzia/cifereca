# Guide autonome — développer un provider Cifereca depuis une capture Browser Recorder

Version documentaire : 2026-11-07. **Ce guide explique les règles ; les signatures exactes du Core exécutant l'export sont dans `plugin-dev-kit/contracts/` et `contract-registry.json`.** Les contrats normatifs du dépôt restent `docs/ARCHITECTURE.md`, `docs/PLUGIN-BOUNDARIES.md` et `cifereca.plugin_api`. Une capture ne prouve pas à elle seule la faisabilité d'un prêt ou d'un téléchargement.

## 1. Décider ce qu'est le provider

- `provider.library` : compte/credential/identité, inventaire personnel des achats ou prêts, recherche si prise en charge, opérations fournisseurs qualifiées. **Une session distincte par credential et identité.**
- `catalog.service` : service technique de recherche/normalisation de notices publiques, sans compte possédé, droits, prêts, placements ni téléchargement implicite.
- `provider.collection` : inventaire virtuel selon son contrat ; ne pas le confondre avec le catalogue public.
- Une **Notice C** décrit un ouvrage ; elle ne prouve ni achat, ni prêt, ni droit, ni octets locaux. Ne jamais déduire une acquisition d'un résultat de recherche, d'un ISBN, d'un titre, d'une couverture, d'une URL de fiche ou d'une disponibilité commerciale.
- Séparer explicitement **catalogue public**, **inventaire personnel authentifié**, **état de prêt/droit**, **offres de livraison**, **réception effective**, **artefact et placement**. Les opérations de consultation n'effectuent ni GET de livraison, ni prêt, ni retour, ni renouvellement.
- Recherche : publier uniquement les champs réellement observés et leur provenance ; pagination, curseurs, filtres, tri, langue, formats, couverture, fraîcheur et caractère partiel doivent être explicités. `HTTP 200` n'est pas une recherche complète ; un résultat vide partiel n'est pas une preuve d'absence. Respecter `ProviderSearchCapabilities`, `SearchResult`, `ItemDetail` et les modèles effectivement exposés par l'API installée.

## 2. Isolation Core / plugin : frontière non négociable

- **Core** : orchestration A→B, sélection d'identité/compte, consentement et révocation, verrouillage et annulation, transport générique via broker, cache transversal/TTL/single-flight, tâches, stockage/SQLite, SHA-256, Artifact, Placement, OriginLink, journal, publication atomique, récupération après crash, gestion des droits d'accès aux profils.
- **Plugin** : protocoles HTTP/HTML/OPDS, parcours SSO/MFA, mapping de champs, identifiants d'édition/branche, observations fournisseurs et leur couverture, offres/capacités, erreurs fournisseurs, éventuels quotas observés. Les transformations DRM utilisent les runtimes et contrats dédiés ; le plugin n'effectue pas de déchiffrement Core caché.
- Imports de production : `from cifereca.plugin_api import ...` et modules internes **du même package plugin**. **Interdit** : `cifereca.storage`, `cifereca.services`, modules `cifereca.core.*` ou GUI privée, SQLite Core, chemins internes, introspection des sessions d'un autre provider. Si un contrat manque, ouvrir un ticket Core et fournir un test de contrat ; ne pas contourner.
- Le Core ne doit connaître ni le nom du site, ni ses routes, ni ses quotas ; il manipule des identifiants opaques et des preuves typées. Ne pas mutualiser un comportement seulement parce que deux sites semblent similaires : vérifier l'équivalence.
- Utiliser les brokers publics disponibles dans `context` et les `requires_brokers` déclarés ; pas de client HTTP global hors contrôle, ni d'import dynamique du code privé Core.

## 3. Identité, provenance, isolation des sessions et caches

- Épingler chaque action à `(identity_id, provider_id, credential_id, account_id, branch, item_or_edition, session_epoch)` selon les champs disponibles ; ne jamais substituer le profil UI actuellement affiché à l'identité sélectionnée lors d'une tâche asynchrone.
- Cloisonner cookies, jetons, SSO, credentials, état de connexion, cache authentifié, pagination, quota, et verrous par compte/tenant/identité. Pas de session HTTP partagée entre établissements ou comptes, même si domaine et moteur sont identiques.
- Cache public : clé tenant/endpoint/requête/locale/version de parseur, TTL et provenance. Cache authentifié : ajouter identité, provider, credential, compte, branche et génération de session. Une erreur/timeout/403 ne doit jamais être remplacé par un ancien succès présenté comme frais.
- Revalider session vivante, identité, credential, compte, consentement et source avant toute opération sensible et avant le commit Core. Rotation ou déconnexion invalide les tâches et projections associées.
- Un même ISBN, titre, SHA, chemin ou fichier sous un autre profil n'établit **aucun** droit. Deux profils peuvent partager l'Artifact global par SHA mais jamais le Placement physique ni l'OriginLink de droit.
- Les identifiants techniques d'établissement, de prêt, de licence, de livre et d'édition sont des dimensions distinctes : ne pas les confondre ni les reconstruire à partir de titres.

## 4. Prêts, achats, offres, DRM : sémantique d'autorité

- Un inventaire de **prêts actifs** lu et interprété avec succès pour le bon compte/branche, y compris vide, et avec pagination requise complète, peut constater la fin d'un prêt précédemment actif **de ce scope**. Lecture partielle, cache, changement de compte, erreur, annulation ou pagination inachevée : aucune clôture par absence.
- Un achat absent du catalogue personnel ne révoque **jamais** automatiquement le droit historique, les octets ni leur provenance. Les acquisitions et prêts ont des règles d'absence différentes.
- Distinguer : droit descriptif `unknown`, refus explicite, offre de livraison positivement attestée, et téléchargement effectivement reçu. Un droit descriptif facultatif inconnu n'interdit pas une capacité de livraison indépendante prouvée ; une offre inconnue n'est pas une autorisation. Respecter les refus explicites.
- Aucun prêt/retour/renouvellement/GET d'ACSM ou d'EPUB par défaut : action et consentement Core explicites, garde contre double POST/GET, idempotence/reprise et preuves de réception. Un ACSM, un HTTP 200 ou une ligne legacy `complete` ne prouve pas un EPUB présent.
- Le Core publie après traitement effectif : SourceReference exacte → preuve de livraison → octets finaux vérifiés → SHA → Artifact/Placement/OriginLink. Les artefacts DRM/ADEPT/LCP et licences ne doivent pas être interprétés comme des fichiers lisibles sans validation du runtime correspondant.
- Les quotas, durées et limites sont des **observations du compte ou de l'établissement**, avec source/date/couverture. Une limite annoncée n'est ni une permission d'emprunter ni une disponibilité instantanée.

## 5. Bundles multi-établissements (exemple BiblioAccess)

Un package `provider-biblioaccess` peut déclarer plusieurs entrées `plugins[]` de type `provider.library` dans **un seul** `cifereca-plugin.json`. Chaque entrypoint est un wrapper mince autour du moteur du même package. Les providers `provider.biblioaccess.place-des-arts` et `provider.biblioaccess.la-rochelle` ont chacun leur schéma de configuration, credential, SSO, tenant et session. `1717` est attesté pour Place des Arts ; l'identifiant La Rochelle doit être relevé, pas deviné. Ne pas assimiler BiblioAccess prêt à Numilog achats.

Les manifests supportent `requires`, `requires_contracts`, `requires_brokers`, `config_schema` et les versions : ne déclarer une capacité qu'après preuve, implémentation et tests. Ne pas créer un provider générique supplémentaire visible à l'utilisateur uniquement pour partager du code. Si le moteur est dans un package distinct, utiliser une dépendance et un contrat technique versionné plutôt qu'un import direct non déclaré.

## 6. Exploiter une capture sans inventer d'API

1. Lire d'abord `plugin-dev-kit/README.md`, `LLM_GUIDE.md`, `contract-registry.json` et `contracts/` ; les versions embarquées font foi. Ce guide n'autorise pas l'utilisation d'API absentes du kit.
2. Identifier origine, tenant, redirections SSO, pages publiques, réponses XHR, pagination, fiches, liste de prêts, état de connexion et offres. Séparer les faits observés des hypothèses ; relever les corps absents, masqués, compressés ou hors origine.
3. **ZIP revu** : projections filtrées/anonymisées, potentiellement insuffisantes pour reconstruire les champs ; **ZIP privé** : données personnelles en clair, sans cookies/Authorization collectés, à garder confidentiel. Aucun des deux n'est un HAR complet, ni une preuve de credentials utilisables.
4. Produire une matrice endpoint/méthode/authentification/tenant/paramètres/structure/erreurs/pagination/couverture/état, avec statut `observé`, `supposé` ou `non capturé`. Ne jamais rejouer un POST de prêt ou une URL de téléchargement sur la seule base d'une trace.
5. Écrire fixtures anonymisées et doubles HTTP offline. Qualifier explicitement la compatibilité de deux établissements sur la même séquence, pas seulement leur domaine commun.

## 7. Livraison autonome et recette minimale

- Package : `src/plugins/provider-<nom>/cifereca-plugin.json`, modules d'entrypoint, README d'installation/configuration, tests unitaires, fixtures et tests de contrat public. S'appuyer sur `src/plugins/provider-reference/`, `docs/plugin-examples/provider/` et `scripts/plugin_packaging.py`.
- Tests : plusieurs identités et credentials ; deux tenants sur même domaine ; cookie/SSO/cache isolés ; connexion révoquée ; 403, timeout, redirect externe, CSRF, réponse tronquée/compressée ; pagination vide/partielle ; identifiants d'édition divergents ; même SHA entre profils ; prêt terminé seulement après inventaire complet ; offre inconnue/refus ; crash/replay sans second GET/POST ; annulation ; aucun accès Storage privé.
- Tests catalogue : résultat vide authentique vs incomplet, notices sans droit, filtres/tri/pagination/locale, images et provenance, quotas observés vs autorisations, aucune action mutante lors de recherche/sync catalogue.
- Exécuter tests plugin, tests de contrat Core/plugin, packaging/install/rollback et CI sur HEAD exact ; conserver une matrice de conformité et les limites non qualifiées. Ne pas prétendre qu'une capture valide un prêt réel, une licence DRM ou une acquisition.
- **Ne jamais embarquer dans le ZIP de capture** : secrets, cookies, clés, données Core privées, captures non revues en prétendant qu'elles sont anonymisées, ou une implémentation prétendument exécutable. Le kit est un guide et des contrats publics, pas un accès à un compte.

## Références de travail (dépôt)

`docs/ARCHITECTURE.md`, `docs/PLUGIN-BOUNDARIES.md`, `docs/CORE-P1-2385-CATALOGUE-INVENTORY-CONTRACT.md`, `docs/PLUGIN-SEARCH-CAPABILITIES-1917.md`, `docs/CORE-P1-3119-LOAN-INVENTORY-CONTRACT.md`, `docs/PLUGINS-P0-3628-LOAN-PRODUCERS-TYPED-MIGRATION.md`, `src/plugins/browser-recorder/README.md`. Tickets : #3171, #3196, #2385, #3252, #2056, #3691. Les documents historiques décrivent parfois un état partiel : vérifier la version du Core exécutant la capture.
