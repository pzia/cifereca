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

Une Sync explicitement scopée conserve `(identity_id, provider_id, credential_id, account_id)` indépendamment du profil affiché, y compris pendant une lecture ou une livraison asynchrone. Sa génération Core `_source_context_epoch` est révoquée avant déconnexion/reconnexion, fermeture ou reconfiguration ; `_account_context_epoch` continue d'invalider les projections et recherches liées à la sélection UI. Les contrôles du compte/session vivant, de la liaison SQLite, du consentement et des octets restent obligatoires jusqu'au commit. Un plugin qui vérifie le propriétaire appelle `IdentityBroker.accounts(identity_id)` avec l'identité épinglée : sans argument, le broker expose seulement le profil UI courant. Tests : `test_source_sync_selection_independence.py`.

Les sous-répertoires d'un profil (`numilog/acsm-…`, `manual/`, `other/` ou autre) ne changent pas la satisfaction d'une référence exacte. Six achats déjà attestés peuvent être reconnus avec zéro GET ; trois fichiers sans origine attestée restent insuffisants pour éviter une acquisition autorisée. Après livraison, le SHA final réutilise l'Artifact existant et ajoute les origines exactes, sans supprimer de copie physique ni attribuer le fournisseur d'après le dossier. Recette synthétique à deux fournisseurs : `test_source_convergence_nested_purchases.py` ; elle ne répare ni ne certifie les six fichiers d'une base de production non fournie.

Pour qualifier un refus Numilog legacy, chercher `local_presence_check` et l'`item_id` exact dans le journal brut : scope identité/credential/compte, source et `task_ref` lorsqu'existant, garde du reçu, placement/chemin, empreintes attendue et observée quand réellement calculées. `file_unavailable` ne prouve pas l'absence physique ; `file_fingerprint_verified` constate uniquement le contrôle des octets. Seul `verified`, après relecture des preuves et du contexte, qualifie la vérification locale complète. Un reçu incomplet/étranger, plusieurs reçus, un fichier non vérifiable, un changement de preuves et une exception locale restent distingués sans nouvel accès réseau, scan ou acquisition. Les exceptions originales restent dans ce journal, l'annulation se propage ; ces traces ne créent aucun droit ni OriginLink et ne remplacent pas le moteur canonique. Tests : `test_numilog_local_proof_diagnostics.py`.

**Convergence Numilog et consentement de source (#2797/#3171).** Le précontrôle legacy `needs_reconciliation` décrit une preuve locale insuffisante ; ce n'est pas une interdiction définitive d'acquérir. Avec les contrats typés de droits et de livraison installés, l'ouverture, Sync manuelle et Sync globale utilisent l'exécuteur canonique existant sous le même verrou. L'adaptateur de preuves historiques réutilise le vérificateur reçu/compte/placement/SHA et tente d'abord la reprise locale d'un reçu `numilog_index_pending`, sans GET et sans dépendre du sélecteur. Un reçu complet exact revérifié permet de publier son OriginLink historique au chemin courant, dans la transaction canonique ; une déclaration fraîche reste déclarée, sans reçu ni origine fournisseur inventés. Une preuve historique ambiguë ou une reprise refusée ne bloque pas une offre fraîche indépendamment attestée.

Le réglage explicite de téléchargement automatique Numilog vaut consentement pour les achats éligibles de **cette identité, ce fournisseur, ce credential et ce compte**, branche `meslivres`. Il n'est pas recopié en permissions par achat : sa valeur et sa génération persistées sont relues avant/après livraison et dans la transaction de publication. OFF, désactivation, rebind et révocation/réactivation invalident la tentative en cours ; aucune permission n'est transmise par SHA ou à un autre profil. CoreUI indique l'autorisation de source et son point de révocation sans confirmation individuelle supplémentaire. Les anciens paramètres globaux non scopés n'accordent rien. Les sessions dépourvues du contrat de livraison gardent leurs gardes legacy ; aucun contournement du plugin ou des droits n'est ajouté.

Chaque référence non satisfaite passe séparément par droit/offre/consentement, récupération d'une livraison interrompue, puis téléchargement si nécessaire. La publication réutilise l'Artifact par SHA final et conserve les placements et reçus historiques, y compris entre profils possédant des octets identiques. Une édition indisponible reste individuellement pending/failed ; les autres continuent et les notices/droits déjà synchronisés restent acquis. Le quota automatique de huit tentatives par ouverture est conservé ; la reprise d'une livraison déjà reçue ne compte pas comme un nouveau GET. Le journal `canonical_purchase_decision` porte les coordonnées exactes, le statut et le motif, avec le `task_ref` existant ; les refus locaux détaillés restent dans `local_presence_check`. Recette déterministe : `test_numilog_canonical_fallback.py` (6/6, sous-répertoires, repli 3/6, SHA identiques, profils isolés, ON/OFF, reprise locale, interruption, redémarrage et trois entrées Sync). **Cette recette synthétique ne certifie pas les six fichiers réels : sans leurs lignes SQLite et empreintes, la cause locale précise des trois refus historiques reste à relever au prochain passage.**

**Plugins** : inventaires/SourceReference et preuves fournisseur/branche, offres et protocoles d'acquisition, prérequis ADEPT propres, adaptations exactes via cifereca.plugin_api uniquement ; transport HTTPX Core partagé sauf exceptions explicites. Pas d'import de Storage privé ni de droit fabriqué depuis une Notice.

**CoreUI** : afficher séparément état inventaire/droits (A), notices (C), copie vérifiée/liaison (B) et acquisition. Le succès de lecture distante ne prétend jamais « fichier présent » ; une erreur d'acquisition ne doit pas être présentée comme une absence de droits.

Le catalogue public fournit des Notice descriptives et éventuellement un cache, **jamais une bibliothèque possédée**. Un fichier dans manual/, acsm-import/ ou other/ n'acquiert pas une origine fournisseur par son répertoire. Contrats : [PLUGIN-BOUNDARIES.md](PLUGIN-BOUNDARIES.md), [#2385](https://github.com/pzia/cifereca/issues/2385).

## 4. État réel et migration sans double autorité

**État vérifié sur `main` le 29/10/2026, après [#3653](https://github.com/pzia/cifereca/pull/3653) et [#3656](https://github.com/pzia/cifereca/pull/3656).** La cible n'est plus seulement un plan en mémoire : un moteur Core et des transactions SQLite v14 sont intégrés. Cela ne constitue pas une recette fournisseur complète ni la fin de la migration legacy.

| Tranche intégrée | Point d'entrée / preuve de code | Limite |
| --- | --- | --- |
| Admission des observations exactes et persistance positive A | `core/rights_snapshot_read.py`, `core/canonical_sync_stage.py` ; tests `test_core_p0_3171_canonical_sync_stage.py` | Un inventaire partiel ne clôt aucun droit ; les fournisseurs sans ABI optionnelle restent compatibles. |
| Vérification fraîche B par profil et exécution de convergence | `core/convergence_verified_presence.py`, `core/convergence_execution.py`, `core/canonical_selected_offers.py` ; tests SQLite de deux faux fournisseurs | Ni déclaration manuelle ni titre/SHA isolé ne remplace OriginLink(provider). Offre sélectionnée et consentement exact revalidés ; aucune acquisition implicite depuis les préférences legacy. |
| Publication et reprise | `core/canonical_publication.py`, `core/canonical_recovery.py` ; `test_core_p0_3171_atomic_publication_v14.py`, tests de crash/reprise | Réception finale et SHA/Placement/OriginLink publiés sous les gardes Core ; un ACSM ou HTTP seul ne vaut pas livre. Cela ne prouve pas un fulfillment réel de tous les fournisseurs. |
| Sync d'ouverture et explicite | `core/sync_coordination.py`, PR #3653 ; `test_core_p0_3171_opening_convergence_executor.py` | Même exécuteur sous verrou, deuxième ouverture zéro GET dans les tests ; les parcours spécialisés legacy subsistent tant que leur migration n'est pas reçue. |
| Consentement persistant et révocable | `core/source_acquisition_consent.py`, adaptateur `numilog_materialization_consent.py`, broker Sync et `gui/canonical_consent_controls.py` | Permissions exactes et réglage automatique de source Numilog ; aucun GET au clic. Le réglage de source évite une confirmation par achat. Réception des six fichiers réels à confirmer séparément. |
| Transfert physique ponctuel A→B→C | `core/canonical_transfer.py`, vrai `LibraryService.materialize_to_identity` ; `test_core_p1_3275_canonical_transfer_v14.py`, `test_core_p1_3275_live_canonical_transfer.py` | Les tests synthétiques SQLite/FS vérifient 1 Artifact, 3 Placements et 8 liens ; ils ne démontrent pas une recette Kobo/ADEPT réelle. |

**Migration encore ouverte :** `entitlements`, `acquisition_ledger` et `backup_items` existent toujours. Le chemin canonique n'écrit pas `backup_items` et ne s'en sert pas pour établir la présence ou le droit ; des lecteurs/writers spécialisés Kobo et gardes conservatoires restent à remplacer dans [#3196](https://github.com/pzia/cifereca/issues/3196), sans perdre leurs preuves historiques. Ne pas supprimer la table, désactiver les droits ou retirer un garde de fichiers sous prétexte que le nouveau moteur est intégré. Le nombre d'éléments d'un manifest legacy ne prouve pas le nombre de SourceReferences admises.

**Réception encore ouverte :** [#3171](https://github.com/pzia/cifereca/issues/3171) conserve la convergence et la recette fournisseurs/ACSM ; [#3642](https://github.com/pzia/cifereca/issues/3642) le cycle sûr sur le volume Ubuntu réel ; [#3197](https://github.com/pzia/cifereca/issues/3197)/[#3118](https://github.com/pzia/cifereca/issues/3118) les prêts et scopes ; [#976](https://github.com/pzia/cifereca/issues/976)/[#3228](https://github.com/pzia/cifereca/issues/3228) les cas ADEPT/BnF. [#2797](https://github.com/pzia/cifereca/issues/2797)/[#2847](https://github.com/pzia/cifereca/issues/2847)/[#3185](https://github.com/pzia/cifereca/issues/3185) gardent le rattrapage et les mutations sûres, [#3275](https://github.com/pzia/cifereca/issues/3275) les transferts et leur réception. Consulter les HEAD/CI des PR ouvertes : aucune tranche en cours ne doit être présumée intégrée.

Les anciens guides [projection backup](CORE-P1-3196-BACKUP-PROJECTION-AUDIT.md), [Kobo réconciliation](KOBO-LOCAL-RECONCILIATION.md), [matérialisation confirmée](SOURCE-MATERIALIZATION-CONFIRMATION.md), [prêts](CORE-P1-3119-LOAN-INVENTORY-CONTRACT.md) et [catalogue](CORE-P1-2385-CATALOGUE-INVENTORY-CONTRACT.md) décrivent des opérations **historiques/partielles** ; ils restent utilisables localement tant que les parcours existent, mais **ne remplacent pas le contrat directeur**.

## 5. Arbre actuel, tests et simulation

- Core applicatif : **src/cifereca/** ; moteur général sous core/, interface publique sous plugin_api/, CoreUI sous gui/ et builtin_ui/.
- **src/cifereca/plugins/** est le framework/loader/manager, **pas** le dépôt des plugins fonctionnels.
- Plugins fonctionnels et leurs tests fournisseur : **src/plugins/** ; le répertoire racine plugins/ n'est pas une source active.
- Paris : **src/plugins/provider-paris/** ; le répertoire src/cifereca/paris/ est absent du tree actuel. src/cifereca/kobo/ garde de l'orchestration résiduelle Core.
- Tests Core et invariants : tests/ ; simulation fonctionnelle **déjà existante** : simulation/ ; environnements/ sert aux dépendances machines, GTK et runtime.

Voir [SIMULATION.md](SIMULATION.md), [TEST-ORGANIZATION.md](TEST-ORGANIZATION.md) et [RATIONALIZATION-STATUS.md](RATIONALIZATION-STATUS.md).

**Recette cible** : ≥2 faux providers interchangeables et adaptateurs réels, 0/1/N, même droit/édition sur branches différentes, 2 comptes/profils avec SHA égal et droits distincts, même titre/SHA divergents, inventaires partiels, transfert A→B→C et huit liens persistés, suppression B/C, import manuel, ADEPT, conversion, fichier déplacé, crash FS/SQLite et seconde Sync sans mutation/téléchargement inutile. Ces exigences ne sont **pas** des affirmations de tests déjà passés.
