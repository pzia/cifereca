# #6002 — Accueil : prêts numériques disponibles (tranche 1)

## État de preuve réellement conservé par Core

Le dépôt ne persiste pas encore un **reçu d'inventaire typé, complet,
non périmé, par identité+fournisseur+credential+emprunteur+branche**
apte à une projection de places libres. Les statistiques
`loan_statistics:<identity>` retiennent des empreintes de prêts positifs et
leur date, mais **aucune preuve de pagination complète** par compte ;
`last_loans` est un cache de lecture. Les plafonds de #4065 sont une
**préférence de l'utilisateur** ; même si la synchronisation La Rochelle
vient de réussir (#5538), cela n'atteste pas un quota du fournisseur ni
l'éligibilité à un livre donné.

**Décision de sûreté :** les projections réellement émises par cette version
sont toutes `status="unknown"`, `remaining=None`,
`eligibility="not_verified"`. Une valeur « 4 − 2 = 2 disponibles »,
« 0 disponibles » ou une somme globale est interdite. Un résultat
`complete` transitoire d'une session HTTP non persistée n'est pas réutilisé
dans l'Accueil.

## Contrat de lecture

- `DigitalLoanAvailabilityReadBroker.digital_loan_availability(identity_id)` :
  facette facultative sur le broker `identities`, sans changer les méthodes
  obligatoires de `IdentityBroker` ; impose une identité explicite et retourne
  `tuple[DigitalLoanAvailability, ...]`. Le plugin Accueil utilise **le broker
  public**, le fallback natif passe par le même adaptateur et le même
  formateur `digital_loan_availability_lines`.
- Les coordonnées `identity/provider/credential/borrower/digital`
  restent typées et distinctes ; credential et borrower exact ne sont jamais
  imprimés comme titre de carte. Une clé du registre de sessions
  `(provider, credential)` partagée entre deux profils est
  `scope_ambiguous` pour chacun (même si un seul des deux paraît
  connecté) ; les connexions indépendantes restent visibles. L'énumération s'appuie sur les comptes
  **configurés** du profil exact et sur les comptes en mémoire,
  **sans appel** au fournisseur, à Sync, à l'acquisition ou à l'ADEPT.
- Les méta-attributs purement présentatifs, ajoutés à
  `AccountOptionField.advisory_loan_medium` et
  `BorrowerScopedOptionField.advisory_loan_limit`, identifient les préférences
  déjà existantes ; le Core n'a aucune table `if provider == …`.
  BnF/La Rochelle : principal numérique ; Paris : principal DILICOM et
  lié `digital`, indépendamment de `physical`. Les valeurs de sauvegarde
  invalides ne sont pas remplacées par le défaut 4.
- Le nombre affiché de prêts numériques actifs est **positif seulement**,
  calculé dans le cache actuel pour le même emprunteur strictement identifié.
  Zéro ligne, compte déconnecté, compte non résolu ou source inconnue
  ne prouvent ni zéro prêt ni quota libre.
- L'interface ne montre ces informations **que pour le profil global courant**.
  Les autres cartes Accueil restent visibles sans agréger leurs droits.
  En cas de broker ancien/échec/absence de compte : « reste inconnu ».
  Total toujours « non calculable », et l'éligibilité « non vérifiée ».
- Le formateur est commun aux deux vues et ne reproduit aucune URL, token
  ni identifiant technique. Aucun bouton ne propose un emprunt.

## Tests et critères complémentaires

Corpus synthétique : plusieurs connexions d'un même fournisseur,
principal+lié, deux identités, 0/4/99, prêts numérique/physique et répliqués,
source non connectée/désactivée/inconnue, cache partiel, faux quota,
opt-in sans droit ; strictement aucune requête fournisseur ; tests
GTK de visibilité et bascule A→B, plus fallback natif. Gates CI/GTK/packaging
sur SHA exact, sans présumer le succès.

**Reste pour clôturer #6002 :** produire un reçu durable de lecture
*complete* par emprunteur+branche avec date/fraîcheur/révocation liée
au compte ; ne calculer une estimation qu'après admission de ce reçu exact
et d'un plafond personnel non ambigu. Brancher indépendamment toute
véritable observation serveur de quota et abonnement attestés ; n'afficher un
total que si toutes les lignes exactes sont comptabilisables. Aucun producteur
de `remaining_loans` fournisseur n'est revendiqué dans cette tranche.

La question de groupe multi-profils demeure #4747 P2 ; l'ajout de ces
champs de présentation ne modifie aucun emprunt ni la dette P0 #5975.


## Contrat de calcul sans preuve inventée — #6002, clarification du 14/11/2026

La politique métier demandée est désormais encodée dans
`src/cifereca/core/digital_loan_remaining_policy.py`, **sans brancher**
la production de valeurs positives au tableau de bord existant :

1. Un **nombre restant fourni directement par le fournisseur** (`kind=remaining`),
   exact, frais et de même epoch, a priorité absolue. Il peut être présenté
   même si l'inventaire détaillé des prêts est incomplet.
2. Sinon, un **quota total fourni par le fournisseur** (`kind=total`) et
   un inventaire numérique exact, durable, `success`, exhaustif,
   paginé jusqu'au terme et frais autorisent le calcul
   `max(0, quota_fournisseur − prêts_actifs_distincts)`.
3. Sinon, un **plafond personnel** de 0 à 99, sous les mêmes conditions
   de complétude d'inventaire, donne une **estimation** distincte.
4. Sinon, le résultat reste **inconnu**. Zéro n'est jamais déduit du manque
   de prêts observés. Toutes les décisions gardent
   `eligibility=not_verified`.

Les DTO de preuve demandent **la portée complète**
`identity/provider/credential/account/borrower/digital`, un instant UTC
avec fuseau, une epoch de révocation et une fraîcheur explicite ; l'inventaire
doit être marqué `durably_committed=True` **après** engagement prouvé par
le producteur, pas au seul terme d'une lecture réseau. Les identifiants
de prêts sont dédupliqués pour éviter les doubles soustractions. Aucun
champ de compte ou titre brut ne sert au calcul.

**Frontière de livraison :** ce code est un calculateur **pur**, et le fait
de construire une instance `ProviderCapacityEvidence` ou
`DigitalInventoryReceipt` ne prouve pas l'observation fournisseur.
Aucun producteur durable validé n'est encore connecté ici :
`DigitalLoanAvailability` et le rendu Accueil continuent donc d'afficher
`Reste inconnu`, avec l'éligibilité non vérifiée. Ne jamais
convertir `last_loans`, les statistiques positives ou un simple résultat
`LoanInventorySnapshot.complete` transitoire en preuve durable. Le
raccordement futur exige persistance atomique + provenance fournisseur +
pagination/branche complète + époque/TTL/révocation et tests de lecture
réelle. **Ce lot ne ferme pas #6002**.

Tests purs : `tests/test_core_p1_6002_remaining_policy.py`, notamment
direct=2 même sur inventaire partiel, direct=0 malgré plafond manuel=4,
quota fournisseur 3−2 contre manuel=99, manuel 4−2 si fournisseur absent,
inventaire incomplet/périmé/cross-account → inconnu, duplication des
identifiants prêt, epoch erronée et absence de quota.
