# P1 #3121 — admission explicite, scope Core et résultat distant

**Portée : contrat additif et durcissement du staging ACSM BnF**, sans refonte
Paris/HTTPX, sans nouvelle capacité publique BnF, sans changement du flux ADEPT.

## Contrat `cifereca.loan.action/1`

`cifereca.plugin_api.lending_actions` sépare trois notions :

1. **Admission indicative** : `assess_lending_action` exige la disponibilité
   réelle `LendingOperationFact` (manifest, descripteur, override, session),
   un sujet exact et un emprunteur connu, une permission métier **attestée
   indépendamment**, un consentement booléen strict et une origine permise.
   Ce calcul ne fait **aucun I/O** et ne donne jamais lui-même l'autorisation
   d'envoyer une commande.
2. **Origine** : `LendingActionOriginPolicy` refuse par défaut les actions
   `opening` et `scheduled` ; la seule politique par défaut est `manual`,
   sous confirmation et permission strictes. Les origines automatiques ne
   peuvent jamais déclencher `loan.borrow/return/renew` par ce contrat.
   La lecture de jeton reste opt-in explicite, distincte du fulfillment.
   Le parcours d'ouverture Paris existant continue d'utiliser son propre
   garde `opening_loan_auto_acquisition` et ses vérifications ADEPT.
3. **Issue** : `LendingActionOutcome` publie un état fermé
   `unsupported|blocked|cancelled|failed|submitted|confirmed|verification_unavailable`
   avec une preuve distincte `none|provider_accepted|provider_verified`.
   `submitted` nécessite une réponse fournisseur acceptée ; `confirmed`
   nécessite une vérification **indépendante du résultat distant**.
   Un HTTP 200 ou une réception ACSM n'est pas une preuve de retour/emprunt.
   Ces objets ne sont **pas** des titres d'accès et ne déclenchent aucune
   mutation de droits.

`loan.borrow` exige une notice exacte (la permission d'emprunter doit
être prouvée séparément) ; les actions sur prêt existant exigent une
`Loan` active liée à l'identité/provider/credential/borrower/loan exact.
L'alias Paris `primary` n'est accepté que pour le véritable compte
principal vide. Un bouton Web, `Loan.can_return`, `Loan.renewable`,
une URL ACSM, `sync.loans` ou un fichier local ne prouvent ni droit
ni consentement.

## Core : bail de portée `LoanActionScopeLease`

`core/loan_action_scope.py` capture la portée et les générations
**avant** le réseau puis vérifie sans I/O :

- identité sélectionnée, association persistée de la connexion,
  présence de l'identité et du compte **objet** exact ;
- prêt inchangé (id, record, compte, credential, medium, état) ;
- session toujours enregistrée et connectée, credential/provider stables,
  emprunteur encore présent dans la session ;
- `_account_context_epoch` Core et `_epoch` propre au provider, si présent.
- inventaire d'emprunteurs lu **directement depuis `_accounts` en mémoire**, uniquement
  si `session.accounts` reste la propriété de base `ProviderSession`.
  Toute surcharge, absence de registre, duplication de l'objet ou dépassement
  de 512 comptes bloque le bail **sans exécuter de getter plugin**.


Un aller-retour Alice → Bob → Alice ne renouvelle pas un ancien consentement,
même si le même objet `Account` est encore visible. Toute erreur échoue
fermée et ne publie aucune valeur privée dans le résultat.

## BnF ACSM privé : intégration réelle

`stage_bnf_loan_acsm` continue d'exiger `confirmed is True`, le prêt actif
exact, la source connectée, puis la vérification live du prêt côté HTTPX.
Le nouveau bail bloque un GET si le contexte a changé pendant la préparation ;
il révoque et nettoie **uniquement** le candidat ACSM créé dans le staging
privé si la portée/epoch change pendant le GET ou la validation du jeton.
Le traitement du jeton n'est ni un emprunt, ni un retour, ni ADEPT. La méthode
interne BnF `download_digital(explicit_user_action=True)` demeure non
déclarée en `acquisition.download` publique. Les deux confirmations
CoreUI sont préservées.

## Tests et périmètre restant

- `tests/test_core_p1_3121_lending_action_admission.py` : capacités,
  confirmation stricte, permission indépendante, isolation borrower, politique
  manual/opening/scheduled, preuve distante vs soumission.
- `tests/test_core_p0_2055_bnf_explicit_stage.py` : tests additionnels
  epoch Core et provider, ABA, changement in-place du credential/borrower,
  remplacement de session.accounts, mutation du prêt, annulation du contexte
  avant le GET, candidate nettoyée.
- `tests/test_core_p1_3121_action_lease_getter.py` : propriété `accounts`
  malveillante non invoquée, enregistrement absent ou dupliqué, limites,
  revalidation après changement du compte.
- Fixtures HTTPX locales seulement, pas de navigateur, réseau live, DRM ni
  nouvel appel implicite.

**Restant avant clôture #3121 :** intégration du contrat d'admission aux
actions publiques Paris (avec maintien des garanties de claim durable
at-most-once de #1120), révalidation des retours et renouvellements après I/O,
qualification réelle BnF dans #2055, puis recette UI/rollback #3122.
Le P0 #2975 reste propriétaire de la sémantique de clôture des droits.
