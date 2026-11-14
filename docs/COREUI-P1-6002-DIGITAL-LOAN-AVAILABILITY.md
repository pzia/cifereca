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

- `IdentityBroker.digital_loan_availability(identity_id)` : extension
  facultative et rétrocompatible ; impose une identité explicite et retourne
  `tuple[DigitalLoanAvailability, ...]`. Le plugin Accueil utilise **le broker
  public**, le fallback natif passe par le même adaptateur et le même
  formateur `digital_loan_availability_lines`.
- Les coordonnées `identity/provider/credential/borrower/digital`
  restent typées et distinctes ; credential et borrower exact ne sont jamais
  imprimés comme titre de carte. L'énumération s'appuie sur les comptes
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
