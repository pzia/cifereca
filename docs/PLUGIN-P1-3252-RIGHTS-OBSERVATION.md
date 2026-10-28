# P1 #3252 — contrat d'observation des droits fournisseurs (v1)

**État au 28/10/2026 : contrat public et adaptateurs réels Kobo, Numilog, Paris et BnF-Prêt déjà fusionnés, en LECTURE SEULE.** PR #3497 (contrat), #3505 (Numilog), #3508 (Kobo), #3524 (BnF) et #3525 (Paris). Les observations sont positives et généralement partielles ; elles ne prouvent aucun fichier et n'autorisent pas de droit/acquisition. Le consommateur transactionnel Core #3171, l'autorité OriginLink/Placement et la migration #3196 ne sont **pas livrés**. Voir le [contrat architectural normatif](ARCHITECTURE.md).

## Frontière publique

Disponible dans `cifereca.plugin_api` :

- `RIGHTS_OBSERVATION_CONTRACT = "cifereca.rights.observation/1"`.
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
un identifiant d'offre observée ET un droit actif non legacy. Une offre n'est
pas une livraison, une sauvegarde locale, un SHA vérifié ni une acquisition.
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
vérifier session courante, scope, epoch, preuve de fichier, permission,
consentement et transaction. Ne pas publier de jeton/cookie ou URL signée
comme `evidence_ref` ; utiliser une référence opaque locale à l'observation.

## État des adaptateurs et travail restant

- **Kobo et Numilog : livrés** (#3508/#3505). Les méthodes optionnelles rights_observation_snapshot(account) produisent des lignes issues des bibliothèques authentifiées, scopes et comptes exacts, couverture partial ; elles n'exécutent pas de sauvegarde.
- **Paris et BnF-Prêt : livrés** (#3525/#3524) pour leurs observations/portées de prêts, sans autorisation de mutation. Le contrat LoanInventorySnapshot reste distinct et sa couverture par branche ne se déduit pas d'une liste vide.
- **Core #3171 : non livré de bout en bout.** PR #3528 a **fusionné** son lecteur explicitement scopé et PR #3523 reste **ouverte** avec un plan de présence en mémoire. Aucun de ces lots ne publie encore l'Artifact/Placement/OriginLink canonique en SQLite ni ne remplace les parcours des fournisseurs.
- **Reste #3252** : compléter uniquement preuves/branches/offres propres aux adaptateurs, sans déporter la décision transactionnelle dans un plugin. Idempotence et mêmes droits sur plusieurs branches doivent être vérifiés avec comptes distincts et sans inférence par Notice.

Une offre annoncée et un snapshot complet d'une seule branche ne valent ni droit d'une autre branche, ni livre téléchargé, ni absence fournisseur globale. La synchronisation automatique unique A→B est décrite dans ARCHITECTURE.md ; la réconciliation manuelle #2797 est un rattrapage exceptionnel.
