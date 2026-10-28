# Architecture CIFERECA — référence normative du 28 octobre 2026

**Contrat directeur : [#3171](https://github.com/pzia/cifereca/issues/3171), décision du propriétaire du 28/10/2026.** Cette page définit **la cible**, et non un moteur déjà intégralement livré. Les opérations legacy et leurs gardes métier restent actives jusqu'à migration démontrée. Contrat indépendant des logs : [LOGGING-CONTRACT.md](LOGGING-CONTRACT.md).

## 1. Parcours nominal : UNE synchronisation automatique A → B

La synchronisation d'un achat ou prêt numérique est un seul parcours métier :
1. **A : observer** le droit exact dans le compte fournisseur, en distinguant identité, provider, credential, compte, **branche**, édition/item, achat/prêt, état actif réellement attesté, époque/session et couverture de lecture. Un inventaire partiel ou inconnu conserve des **positifs seulement** ; une omission n'est jamais preuve d'expiration, de retour ni de révocation.
2. **B : constater la présence** à travers la chaîne SourceReference → OriginLink(provider) attesté → SHA du fichier final → Placement **physiquement revalidé dans le profil cible**. Un même titre, ISBN, édition bibliographique, chemin ou SHA dans un autre profil n'identifie ni la provenance ni un droit.
3. **Acquérir si non satisfait** : sans preuve d'une copie locale adéquate, tenter une nouvelle acquisition dans la capacité, le droit actif, la licence ADEPT, les politiques et consentements déjà autorisés. L'existence d'un livre local similaire n'impose pas de dialogue de réconciliation avant le téléchargement. Un scan incomplet ne prouve pas l'absence, **mais ne doit pas devenir un veto définitif** : une admission indépendante et ses contrôles locaux frais peuvent conduire à l'acquisition autorisée, ou à un refus explicite.
4. **Publier après traitement** : après téléchargement, ACSM/ADEPT et validation du contenu final, calculer son SHA-256, réutiliser le même Artifact global si SHA égal, puis publier de façon atomique Placement physique et OriginLink fournisseur et, lorsque attesté, ADEPT. La réponse HTTP, un ACSM ou un statut de ledger seuls ne valent pas livre acquis. Reprise après crash et deuxième Sync idempotente obligatoires.

**Pas de seconde boucle métier obligatoire de « réconciliation ».** [#2797](https://github.com/pzia/cifereca/issues/2797) porte preuve, diagnostics et **rattrapage exceptionnel** des incohérences, non un préalable universel à la synchronisation. Ne jamais emprunter, renouveler ni retourner automatiquement comme effet de la Sync.

## 2. Les cinq entités canoniques et le journal

| Entité | Autorité et cardinalité | Ne prouve pas |
| --- | --- | --- |
| **Notice (C)** | Métadonnées descriptives d'œuvre/édition, plusieurs notices et provenance **par valeur**. « Fiche Livre » est une vue, pas une sixième autorité. | Droit, possession, provenance de fichier, présence. |
| **SourceReference (A)** | Référence exacte : provider, credential, compte, identité, **branche**, item/édition, achat/prêt et preuve amont. Le fournisseur fait autorité sur le droit et son état. | Octets disponibles ni licence d'un autre compte. |
| **Artifact (B)** | **SHA-256 global des octets finaux**, taille, format, validation ; plusieurs profils, copies et origines peuvent le viser. | Un droit ni une copie accessible dans un profil particulier. |
| **Placement (B)** | Copie physique validée, profil et chemin ; deux profils gardent des Placements distincts, même pour SHA égal. | Origine fournisseur par nom de dossier ou droit transmis. |
| **OriginLink** | Relation persistante, multiple, idempotente, **source/preuve/contexte → SHA** : provider, manual, transfer, cifereca, adept, derived. Garde scope/profil/compte, identifiants attestés, preuve, timestamps et parent pour dérivation. | Autorisation de restauration, droit ou présence actuelle. |
| **Event/acquisition_ledger** | Chronique des opérations, erreurs, reprises et traces. | Autorité concurrente de présence ou de droit. |

Une SourceReference interne Cifereca issue d'un transfert est **ponctuelle par défaut** ; réplication continue seulement sur opt-in explicite. Un OriginLink ADEPT relie la preuve de licence/activation au **SHA final**, jamais à une prétendue licence partagée. Supprimer un Placement laisse intacts Artifact, SourceReference et liens historiques.

### Scénario d'acceptation A → B → C (NON encore prouvé en production)

- A achète L puis le traite avec ADEPT : un Artifact SHA S, un Placement A, deux liens provider A et adept A.
- A envoie une **copie physique indépendante** à B : Placement B, deux liens contextualisés chez B, transfer A→B et cifereca A→B. Aucun droit A n'est transmis.
- B achète indépendamment L ; après ADEPT B, SHA final identique S : **un** Artifact global et deux liens supplémentaires provider B et adept B, associés à son propre droit.
- B envoie à C : Placement C + liens transfer B→C et cifereca B→C.
- Total attendu : **1 Artifact, 3 Placements, 8 OriginLink distincts** (2 provider, 2 adept, 2 transfer, 2 cifereca). Rejouer ces étapes ne crée pas de doubles liens.
- Après suppression locale B/C, retirer ou invalider seulement leurs Placements, jamais les huit liens ni le droit B. B peut réacquérir au titre de son achat ; C n'est pas restauré depuis le seul transfert ponctuel.
- Une conversion EPUB changeant le SHA donne un nouvel Artifact et un lien derived vers le parent (convertisseur, version, paramètres), sans création de droit.

Les huit liens exigent **tests de persistance SQLite, crash-recovery et droits isolés**. Huit objets Python en mémoire ne prouvent pas cette livraison. Un même titre peut recouvrir plusieurs éditions et plusieurs SHA ; un SHA commun n'implique aucune licence ni déduplication de copie inter-profils.

## 3. Responsabilités

**Core** : sélection de sources et de comptes exacts, décision A→B générique, scopes/époques, inspection SHA et Placement, cache/verrous, consentement/admission, transactions, publication et reprise, journal de faits. Il manipule des provider_id opaques, pas la sémantique Kobo/Numilog/BnF.

**Plugins** : inventaires/SourceReference et preuves fournisseur/branche, offres et protocoles d'acquisition, prérequis ADEPT propres, adaptations exactes via cifereca.plugin_api uniquement ; transport HTTPX Core partagé sauf exceptions explicites. Pas d'import de Storage privé ni de droit fabriqué depuis une Notice.

**CoreUI** : afficher séparément état inventaire/droits (A), notices (C), copie vérifiée/liaison (B) et acquisition. Le succès de lecture distante ne prétend jamais « fichier présent » ; une erreur d'acquisition ne doit pas être présentée comme une absence de droits.

Le catalogue public fournit des Notice descriptives et éventuellement un cache, **jamais une bibliothèque possédée**. Un fichier dans manual/, acsm-import/ ou other/ n'acquiert pas une origine fournisseur par son répertoire. Contrats : [PLUGIN-BOUNDARIES.md](PLUGIN-BOUNDARIES.md), [#2385](https://github.com/pzia/cifereca/issues/2385).

## 4. État réel et migration sans double autorité

**Sur main :** tables existantes entitlements, artifacts, placements, acquisition_ledger et projection Kobo 0.7 backup_items, avec des flux encore spécialisés. Ces tables et flux **ne constituent pas encore** le modèle canonique complet ; backup_items ne doit plus devenir une autorité permanente de présence. Les contrats de lecture [#3497](https://github.com/pzia/cifereca/pull/3497) et adaptateurs Kobo/Numilog [#3508](https://github.com/pzia/cifereca/pull/3508)/[#3505](https://github.com/pzia/cifereca/pull/3505), Paris/BnF [#3525](https://github.com/pzia/cifereca/pull/3525)/[#3524](https://github.com/pzia/cifereca/pull/3524) produisent **uniquement des observations read-only**, souvent partielles. Une matrice de capacités n'autorise aucune opération d'acquisition.

**Ouvert / non intégré :** #3171 (moteur A→B), avec PR [#3523](https://github.com/pzia/cifereca/pull/3523) (plan en mémoire) et [#3528](https://github.com/pzia/cifereca/pull/3528) (lecture scopée des snapshots) : **aucune publication transactionnelle d'OriginLink SQLite par ces tranches**. [#3196](https://github.com/pzia/cifereca/issues/3196) possède la migration de backup_items et des tables, sans double écriture permanente ; [#2797](https://github.com/pzia/cifereca/issues/2797)/[#2847](https://github.com/pzia/cifereca/issues/2847) les preuves et chemins morts ; [#3185](https://github.com/pzia/cifereca/issues/3185) les mutations cohérentes ; [#3275](https://github.com/pzia/cifereca/issues/3275) les transferts et origines ; [#3197](https://github.com/pzia/cifereca/issues/3197) la migration des prêts v1 ; [#976](https://github.com/pzia/cifereca/issues/976)/[#3228](https://github.com/pzia/cifereca/issues/3228) les recettes ADEPT/BnF ; [#3416](https://github.com/pzia/cifereca/issues/3416) la présentation UI.

Les anciens guides [projection backup](CORE-P1-3196-BACKUP-PROJECTION-AUDIT.md), [Kobo réconciliation](KOBO-LOCAL-RECONCILIATION.md), [matérialisation confirmée](SOURCE-MATERIALIZATION-CONFIRMATION.md), [prêts](CORE-P1-3119-LOAN-INVENTORY-CONTRACT.md) et [catalogue](CORE-P1-2385-CATALOGUE-INVENTORY-CONTRACT.md) décrivent des opérations **historiques/partielles** ; ils restent utilisables localement tant que les parcours existent, mais **ne remplacent pas le contrat directeur**.

## 5. Arbre actuel, tests et simulation

- Core applicatif : **src/cifereca/** ; moteur général sous core/, interface publique sous plugin_api/, CoreUI sous gui/ et builtin_ui/.
- **src/cifereca/plugins/** est le framework/loader/manager, **pas** le dépôt des plugins fonctionnels.
- Plugins fonctionnels et leurs tests fournisseur : **src/plugins/** ; le répertoire racine plugins/ n'est pas une source active.
- Paris : **src/plugins/provider-paris/** ; le répertoire src/cifereca/paris/ est absent du tree actuel. src/cifereca/kobo/ garde de l'orchestration résiduelle Core.
- Tests Core et invariants : tests/ ; simulation fonctionnelle **déjà existante** : simulation/ ; environnements/ sert aux dépendances machines, GTK et runtime.

Voir [SIMULATION.md](SIMULATION.md), [TEST-ORGANIZATION.md](TEST-ORGANIZATION.md) et [RATIONALIZATION-STATUS.md](RATIONALIZATION-STATUS.md).

**Recette cible** : ≥2 faux providers interchangeables et adaptateurs réels, 0/1/N, même droit/édition sur branches différentes, 2 comptes/profils avec SHA égal et droits distincts, même titre/SHA divergents, inventaires partiels, transfert A→B→C et huit liens persistés, suppression B/C, import manuel, ADEPT, conversion, fichier déplacé, crash FS/SQLite et seconde Sync sans mutation/téléchargement inutile. Ces exigences ne sont **pas** des affirmations de tests déjà passés.
