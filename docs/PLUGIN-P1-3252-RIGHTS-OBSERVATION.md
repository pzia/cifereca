# P1 #3252 — contrat d'observation des droits fournisseurs (v1)

**Tranche additive : contrat public et tests synthétiques uniquement.**
Cette livraison **ne branche encore ni Kobo ni Numilog réels** et ne ferme pas
#3252. Le Core P0 #3171 reste propriétaire des transactions, de l'admission,
des verrous et de la réconciliation; #3275 reste propriétaire des copies entre
identités. Les contrats de prêts `LoanInventorySnapshot` Paris/BnF restent
inchangés.

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
`provider_response` et `coverage=complete`. Les lectures ne déclenchent
ni réseau ni téléchargement. Le consommateur Core doit indépendamment
vérifier session courante, scope, epoch, preuve de fichier, permission,
consentement et transaction. Ne pas publier de jeton/cookie ou URL signée
comme `evidence_ref` ; utiliser une référence opaque locale à l'observation.

## Adaptation réelle nécessaire avant clôture

1. **Plugins Numilog et Kobo** : transformer les inventaires et reçus **réels**
   déjà observés en snapshots par identité/connexion/compte/édition,
   exposer le niveau de couverture réel, conserver les échecs, l'historique,
   la pagination et l'offre de livraison séparée. Ne créer aucun droit Core
   à partir d'une notice. Préserver les transports HTTPX et parcours legacy.
2. **Tests d'adaptateurs indépendants** sur 0/1/N, plusieurs comptes, collisions
   d'édition, retrait/reconnexion, reprise et absence de mutation/sauvegarde
   automatique. Les faux Kobo/Numilog de cette PR ne valent **pas** tests
   des adaptateurs réels.
3. **Core #3171** consommer cette preuve uniquement après comparaison
   d'état/session et décision de réconciliation dans ses brokers autoritaires,
   sans écrire depuis le simple fait de lire le snapshot.

La source de vérité du droit et la preuve d'acquisition ne se déduisent
jamais d'une couverture globale « complete » d'une bibliothèque personnelle.
