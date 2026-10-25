# P1 #3121 — admission d'une acquisition de prêt à l'ouverture

## Décision opérationnelle

La découverte d'un prêt **n'accorde jamais** le droit de récupérer
automatiquement l'ACSM, d'appeler ADEPT ou de muter le prêt.

Le Core ne maintient plus une liste noire `loan.source_provider != "bnf-pret"`.
Le contrôle `_opening_loan_acquisition_allowed(loan, session=…)`
requiert à présent simultanément :

- `ProviderCapabilities.opening_loan_auto_acquisition=True` — opt-in
  explicite, **False par défaut** dans toute nouvelle session/plugin ;
- session active, `loans=True` et `digital_download=True` ;
- exactitude fournisseur, credential, identité Cifereca et emprunteur
  dans les comptes actifs de la session enregistrée ;
- prêt numérique avec indice d'acquisition préexistant ;
- nouvelle résolution de la session dans le registre avant chaque
  passage au worker d'acquisition existant.

**Paris :** son descripteur opt-in conserve le parcours automatique
historique, sous les gardes ADEPT / provenance / profil déjà en place.
L'alias de compte principal `primary` n'est autorisé que pour le
compte upstream vide et principal effectivement lié à cette session.
Un compte lié reste distinct.

**BnF Prêt :** opt-in absent et téléchargement public non déclaré :
un prêt HTML positif n'autorise pas d'acquisition à l'ouverture.
Le parcours distinct `stage_bnf_loan_acsm` avec validation et double
confirmation manuelles reste inchangé ; le démarrage n'y accède pas.

**Autres providers de prêt actuels ou futurs :** refus par défaut,
même si `loan.ebook_url` ou `loan.raw` semble indiquer un jeton.
Aucun accès réseau ne découle de l'inspection de la capability.

## Frontières de cette tranche

- **Réalisé :** opt-in d'ouverture, garde de session et compte exact,
  tests hors ligne multi-comptes, déconnexion et URL sans droit.
- **Non réalisé :** matrice générale d'admission manuelle /
  programmée par opération, revalidation du token session/epoch
  **après** les I/O fournisseur, isolation universelle ACSM/ADEPT.
  Ces besoins restent dans #3121/#3122 et les tâches existantes P0.
- **Aucun changement** de l'implémentation de retour / renouvellement,
  du staging BnF, des mutations locales, du moteur ADEPT ou du
  téléchargement manuel. Aucun emprunt ou fulfillment n'est testé en
  direct dans cette tranche.

Le booléen porte une disponibilité déclarative explicite, **pas**
un consentement transférable d'une action à une autre. L'admission
positive doit toujours traverser les gardes de `_acquire_active_loan_automatically`.
