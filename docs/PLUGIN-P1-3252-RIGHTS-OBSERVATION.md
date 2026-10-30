# P1 #3252 — contrat d'observation des droits fournisseurs (v1)

**État au 28/10/2026 :** contrat `rights.observation/1` et adaptateurs réels fusionnés (#3497, #3505, #3508, #3524, #3525). Le Core #3171 dispose **désormais de fondations fusionnées** : admission par scope #3528, plan A→B #3523, AccountRef v2 #3559, publication atomique `Artifact/Placement/OriginLink` #3563, présence physique rehashée #3567, exécuteur sous verrou #3569, point d'extension Sync #3570 et observation commune dans la Sync d'ouverture #3572. **Ces composants seuls n'activent pas la livraison de production** : les callbacks enregistrés de livraison fournisseur et la lecture du consentement *effectivement persisté* restent nécessaires. La migration complète des chemins legacy #3196 et la recette crash/réparation sont distinctes. Voir le [contrat architectural normatif](ARCHITECTURE.md) et la [matrice des offres](../src/plugins/RIGHTS-DELIVERY-OFFERS-3252.md).

## Frontière publique

Disponible dans `cifereca.plugin_api` :

- `RIGHTS_OBSERVATION_CONTRACT = "cifereca.rights.observation/1"` (ABI maintenue ; livraison et droit découplés).
- `ProviderRightsScope(identity_id, provider_id, credential_id, account_id)` :
  portée **exacte** ; un compte principal à ID vide est permis, jamais un joker.
- `ProviderRightObservation(edition_id, right_id, kind, state, evidence_kind,
  evidence_ref, observed_at, delivery, delivery_offer_id)` :
  fait positif **revendiqué** par le fournisseur ou un reçu historique.
- `ProviderRightsSnapshot(scope, rights, status, coverage,
  pagination_complete, branch, observed_at, snapshot_ref, resume_cursor)` :
  enveloppe par compte/credential/branche pour 0/1/N observations.

`provider_response` et `historical_receipt` requièrent une référence
d'observation et une date avec fuseau. Un statut `active` porté par
`legacy_unverified` **n'est pas attesté**. `delivery="available"` exige
un identifiant d'offre exacte ET une preuve `provider_response` récente.
L'état du droit (`active`, `inactive` ou `unknown`) reste facultatif et
ne peut **pas** annuler seul une capacité de livraison positive. Une offre
n'est pas une livraison, une sauvegarde locale, un SHA vérifié ni une acquisition.
Une fiche catalogue/ISBN/titre ne remplit aucun de ces champs par inférence.

Les doublons `(edition_id, right_id, kind)` sont rejetés; plusieurs droits
**distincts** pour une édition restent possibles. Toute pagination partielle
ou reprise par `resume_cursor` reste partielle, sans preuve négative.
Les états `failed/cancelled` ne gardent aucune ligne positive. Même un
snapshot `complete` n'atteste que la **déclaration de couverture de sa
branche**; il ne peut pas effacer un droit manquant.

`authorizes_mutation=False` et
`authorizes_negative_reconciliation=False` sont invariants, même avec
`provider_response` et `coverage=complete`. Construire ou afficher l'enveloppe ne déclenche aucun réseau ; **l'appel explicite à l'adaptateur réel peut effectuer une lecture distante authentifiée**, sans téléchargement d'ouvrage ni effet métier. Le consommateur Core doit indépendamment
vérifier session courante, scope, epoch, preuve de fichier, capacité
réellement offerte, consentement de synchronisation et transaction. Une
observation de droit n'est jamais une autorisation autonome de GET. Ne pas publier de jeton/cookie ou URL signée
comme `evidence_ref` ; utiliser une référence opaque locale à l'observation.

## État des adaptateurs et critères de réception encore ouverts

- **Numilog** : inventaire `MesLivres` positif et partiel (#3505). Scan ordinaire `delivery=unknown` ; préqualification ciblée d'une véritable action signée #3586 et liaison de l'offre à l'identité Core avant GET #3587, fusionnées. Sans callback Core et consentement persisté, aucune acquisition automatique.
- **BnF-Prêt** : présence de la ressource dans `active-loans` (#3524), avec statut de prêt facultatif (`unknown` possible) ; une action de livraison `reader-acs4` authentifiée peut attester une offre opaque liée à la ressource exacte (#3585, fusionnée). Le jeton ACSM ne prouve **pas** les octets EPUB/PDF finaux après ADEPT.
- **Kobo** : manifeste personnel positif (#3508), pouvant exposer une
ressource du compte avec `state=unknown` quand `owned` est absent ; préqualification **explicitement sélectionnée** par un POST Vault `descriptor` puis candidat HTTPS GET strict et revalidation du descripteur privé (#3592, fusionnée). Aucune inférence depuis `downloadable` ou un simple drapeau d'archive. Le POST de préflight n'est permis qu'après admission et consentement Core.
- **Paris** : `ListLoans(DILICOM)` fournit les identifiants de prêt/ressource exacts, et l'état reste `state=unknown`. Un `AdditionalProperties.EbookUrl` privé valide qualifie désormais **une capacité de livraison** `available` avec offre opaque liée au compte, **sans inventer un droit actif** et sans GET de contenu pendant le scan. Les méthodes sélectionnées revalident la ressource/lien et utilisent `download_digital` sous compte exact pour déposer le candidat en staging, sans POST de réemprunt/quota si le lien manque. L'échec HTTP 403 ou ADEPT reste distinct de l'observation initiale. Core #3171 assure consentement, SHA et publication atomique.
- **Core #3171** : les fondations de publication transactionnelle et d'exécution sous verrou sont fusionnées, **pas le routage fournisseur de production**. `src/cifereca/core/sync_coordination.py` n'appelle l'exécuteur que si les deux hooks Core `_canonical_right_delivery` et `_canonical_persisted_consent` existent et sont utilisables. Les tests synthétiques qui leur injectent des fonctions ne prouvent pas leur enregistrement dans une application installée.

**Critères de fermeture #3252 :** une observation A authentifiée sous `AccountRef v2` exact, une offre exécutable `delivery=available` spécifique à la même `SourceKey`, une autorisation/consentement **durablement persisté**, la livraison des octets finaux (ACSM + traitement DRM s'il le faut), un reçu indépendant de A, le SHA final vérifié, puis `Artifact/Placement/OriginLink` publié atomiquement par le Core. La deuxième Sync doit constater un OriginLink et un Placement **rehashés** sans second GET ; disparition dans un inventaire partiel, résultat de catalogue, titre, ISBN ou fichier manuel ne suffisent jamais à créer/supprimer un droit. Vérifier refus avant GET si propriétaire A→B/A→A, consentement retiré ou lien signé périmé, et reprise après crash sans faux B.

**Handoff à Core et Backlog :** les producteurs restent exclusivement dans Plugins #3252 ; l'intégration des hooks réels et les transactions sont à Core #3171/#3196. Ne fermer aucun de ces tickets sur une simulation seule. Aucun P2 ni réconciliation manuelle parallèle.

Une offre annoncée et un snapshot complet d'une seule branche ne valent ni droit d'une autre branche, ni livre téléchargé, ni absence fournisseur globale. La convergence automatique unique A→B reste celle de `ARCHITECTURE.md`, pas une seconde action utilisateur.
