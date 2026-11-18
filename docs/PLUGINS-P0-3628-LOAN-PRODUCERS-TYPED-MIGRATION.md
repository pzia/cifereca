# #3628 · Producteurs de prêts installables — migration vers l'inventaire typé

## Révision de réception #3197 — 17 novembre 2026

L'inventaire initial à deux producteurs est historique. Sur main après #6605,
les trois déclarations installables `sync.loans` sont :

| Package / plugin | Producteur typé | Couverture |
| --- | --- | --- |
| provider-paris / provider.paris | ParisProviderSession.loan_inventory_snapshot | DILICOM / SYRACUSE indépendants ; ne pas inférer de négatif d'une branche inconnue |
| provider-bnf-pret / provider.bnf-pret | BnfProviderSession.loan_inventory_snapshot | HTML partiel ; aucune absence attestée |
| provider-biblioaccess / provider.biblioaccess.la-rochelle | LaRochelleSession.loan_inventory_snapshot | MyLoans authentifié, lignes toutes parsées et absence de pagination attestée : complet pour cette branche ; sinon partiel |

Le quatrième service public `provider.biblioaccess-catalogue.la-rochelle`
n'annonce pas sync.loans. Le runtime ADEPT expose des licences locales, pas
un inventaire de prêts fournisseur. Le test
`tests/test_core_p0_6560_loan_producer_inventory.py` vérifie **tous les manifestes
livrés**, puis les vraies classes de ces trois producteurs, sans constructeur
ni I/O. Un nouveau producteur doit enrichir la matrice et ses preuves.
La conformité de forme ne remplace pas les tests de transport :
Paris/BnF #3628/#3122 et BiblioAccess
`src/plugins/provider-biblioaccess/tests/test_biblioaccess_sso.py`.

### Politique explicite de migration et de retrait (#3197)

- **Plugin API 1.4.8 / major 1.x** : maintenir les points publics historiques
  `ProviderSession.loans(account)`, `legacy_loan_inventory` et
  `read_loan_inventory` pour les extensions installables. La liste v1 exacte
  produit uniquement `partial/legacy_unverified`, sans couverture négative.
  Un snapshot typé absent ou échoué ne doit pas provoquer une deuxième lecture
  après échec du producteur typé. Les chemins métier Core doivent converger
  vers un seul consommateur d'enveloppe ; l'adaptateur public est l'unique
  frontière de compatibilité à terme, pas un deuxième moteur.
- **Cible de retrait : Plugin API 2.0**, après annonce de rupture et migration
  des extensions ; ne pas bump la version ni interrompre une installation
  1.x dans ce lot. Le nouveau producteur implémente
  `loan_inventory_snapshot(account)`, émet une portée exacte, des copies
  défensives, état explicite et couverture par branche. Les erreurs restent
  `failed/cancelled/partial` selon le contrat, jamais une liste vide complète.
  Tester 0/1/N, emprunteurs liés, rebind et absence de double lecture.
- Avant activation 2.0 : inventaire des plugins réellement installés dans le
  runtime de migration, refus explicite des manifestes non compatibles,
  qualification des producteurs et des consumers, installation et rollback.
  Une extension legacy ne doit pas être activée par simple réécriture de son
  manifest. Un rollback restaure les packages/Core compatibles ensemble ; les
  caches historiques ne deviennent pas des preuves négatives.
- Aucune date de release 2.0 n'est décidée ; aucune preuve de l'ensemble
  des plugins tiers privés n'est disponible. La migration Core 1.x et
  l'absence de consommateurs décisionnels v1 doivent être validées avant
  clôture #3197 ; l'extinction physique de l'ABI publique est conditionnée
  à cette version incompatible, non à une suppression silencieuse.

## Périmètre vérifié (30 octobre 2026)

Ticket enfant Plugins P0 de [#3197](https://github.com/pzia/cifereca/issues/3197),
sous l'architecture #3171. Le Core garde **exclusivement** le moteur Sync,
les consommateurs, le stockage canonique, le consentement, le retour et la
politique de suppression des droits.

| Producteur / service installable | Exposition legacy | Observation typée en production | Garantie négative |
| --- | --- | --- | --- |
| `provider-paris` (`ProviderSession`, `capabilities.loans=True`) | `loans(Account)` positif compatible ; aucun `last_loan_errors` depuis #3759 | `loan_inventory_snapshot(Account) -> LoanInventorySnapshot` sous verrou : une lecture DILICOM/digital et SYRACUSE/physical, compte/credential/identité et instance vivante figés, evidence de branche locale au contexte de la lecture. **N'appelle pas `loans()` et ne lit/écrit pas `last_loan_errors`** depuis la voie typée. | **Aucune** : succès des deux branches `completeness=unknown`, échec isolé `partial/branch_unavailable`, deux branches en panne refusées |
| `provider-bnf-pret` (`ProviderSession`, `capabilities.loans=True`) | `loans(Account)`, pas de canal `last_loan_errors` | `loan_inventory_snapshot(Account) -> LoanInventorySnapshot` sous verrou epoch/session, compte lié exact, une seule lecture HTML BnF, **indépendante de la façade v1** ; prêt positif et action ACS4 observables sans récupération du token | **Aucune** : HTML `active-loans` même vide toujours `partial/inventory_not_exhaustive` |
| `runtime-kobo-adept` | `loans(profile)` du moteur technique local ADEPT, **pas** `ProviderSession.loans(Account)` | Inventaire de runtime local distinct ; **pas un producteur de prêts fournisseur** dans la sélection Core `ProviderSession` | Aucune preuve de restitution fournisseur déduite des licences locales |
| Kobo, Numilog, autres providers catalogue/public installables | pas de `ProviderCapabilities.loans=True` identifié en tant que producteur `LoanInventorySnapshot` | Achats : contrat distinct `cifereca.rights.observation/1` ; aucune conversion de liste descriptive en prêt | Aucune |

L'API publique versionnée `cifereca.loan.inventory/1` et le modèle
`LoanInventorySnapshot` restent inchangés. Les deux providers de prêts
font **un seul passage fournisseur** par requête ; il n'y a pas de deuxième
GET `loans()` déclenché par la voie typée.

## Conformité et compatibilité

- Les tableaux `LoanInventorySnapshot` sont des copies défensives scopées
  (`identity_id/provider_id/credential_id/account_id`), couverture par
  branche figée, état `success/partial/failed/cancelled` explicite.
- Paris : le statut de chaque branche est collecté dans un
  `ContextVar` pour **cette lecture**, sous verrou. Les exceptions
  fournisseur DILICOM ou SYRACUSE ne deviennent pas une liste négative.
  La façade mutable `last_loan_errors` est **supprimée** depuis
  [#3759](https://github.com/pzia/cifereca/pull/3759) ; la méthode
  `loans(Account)` reste compatible pour les prêts positifs sans
  attestation négative. L'annulation arrête les lectures sans fabriquer de
  résultat complet.
- BnF : typé et legacy partagent seulement un lecteur interne verrouillé ;
  le modèle de droits positifs reste identique. Un HTML 200 vide ne suffit
  pas à prouver un retour de prêt.
- Aucune offre `EbookUrl`, notice, titre, ISBN, chemin ACSM ou action HTTP
  ne produit implicitement une présence locale, une absence de droit, un
  `OriginLink`, une acquisition ni une confirmation de retour.

**Tests de réception Plugins** (clients synthétiques, sans compte réel) :
`provider-paris/tests/test_p0_3628_paris_typed_no_v1_errors.py`,
`provider-bnf-pret/tests/test_p0_3628_bnf_typed_producer_independent_v1.py`,
plus suites antérieures de 0/1/N, erreurs HTTP, réseau interrompu,
Session `connect/close`, rebind A→B/A→A, identité/credential croisés,
deux branches indépendantes, protection contre lecture GET en double,
cache et sorties typées partielles. Les assertions des anciens tests de
course ont été déplacées sur le lecteur privé commun plutôt que de
réintroduire le couplage métier au lecteur v1.

## Handoff historique à l'agent A · #3197 (état au 30/10/2026)

L'état ci-dessous est conservé comme historique de migration, non comme
inventaire courant. #6616 a unifié le cache direct, #6631 le Sync explicite
et planifié. Le dernier cutover opening supprime le choix Core de la voie v1 :
les consommateurs passent par `read_loan_inventory`, avec le lecteur legacy
uniquement derrière l'adaptateur public Plugin API 1.x. Un compte sans
capacité prêts ne déclenche aucun GET. Le retrait de l'ABI publique reste
conditionné à la major 2.0. La recette de fin de lot doit attester le HEAD
fusionné avant fermeture de #3197.

**Rester à enlever chez Core seulement après vérification de la
compatibilité des extensions installées et migration/major Plugin API :**

1. `src/cifereca/core/sync_coordination.py` :
   `_sync_loan_inventory_observation` choisit encore un lecteur
   `_loans_for_account(..., force=True)` si le snapshot typé manque.
2. `src/cifereca/services.py` : `_loans_for_account` conserve le
   fallback `session.loans(account)` et le cache v1.
3. `src/cifereca/core/loan_return_verification.py` :
   `_read_paris_return_presence` accepte encore les positifs legacy,
   sans jamais les traiter comme une preuve négative.
4. `src/cifereca/plugin_api/loan_inventory.py` :
   `read_loan_inventory` adapte encore les anciens plugins non typés
   via `legacy_loan_inventory(..., status="partial")`.

**Ne pas supprimer ces fallbacks dans #3628** : il faut d'abord une
règle de compatibilité explicite pour tous les plugins installés,
un chemin de migration/rollback, et les preuves Core Sync/ouverture/
retour correspondantes. Le P0 parent #3197 reste piloté par A.

### Migration du dispatch d'ouverture (#3197, 18/11/2026)

Un producteur qui expose le trio canonique complet ne peut plus revenir au
worker historique d'ouverture, y compris si A est partiel/échoue, si
l'utilisateur n'a pas encore configuré son CAS ou si sa politique est OFF.
Le réglage historique d'authentification d'un compte, ni la déclaration
de capacité unattended, ne vaut un CAS ON : le passage au moteur canonique
nécessite l'action explicite **Activer l'acquisition automatique** dans
Synchronisation. L'inventaire authentifié continue sans livraison si la
politique est absente. Ne pas changer silencieusement un OFF, et ne pas
réécrire les anciennes préférences ni historiques ADEPT.

Un ancien reçu `loan_auto / kobo-adept / attention_required` de ce prêt
exact reste un veto de rejeu : identité, source, credential, emprunteur
(alias primaire déclaré par le descriptor seulement), édition et loan ID.
Les anciens reçus insuffisamment documentés sous la même portée restent
conservateurs. Un autre prêt ou un autre compte n'hérite pas de ce veto.
Aucun ancien reçu ne prouve B ou une permission positive. Un checkpoint
canonique de livraison réellement achevée peut être repris sans GET/ADEPT.
Le parcours explicite historique de remédiation reste disponible.

Limites : le fallback d'ouverture reste réservé aux anciens plugins 1.x
sans trio complet, et le writer historique peut encore servir une
remédiation explicitement demandée. Ne pas le retirer avant équivalence
des reçus de reprise et des refus après un échec ADEPT canonique en vol.
Les états anciens ne sont ni effacés ni transformés en présence attestée.
