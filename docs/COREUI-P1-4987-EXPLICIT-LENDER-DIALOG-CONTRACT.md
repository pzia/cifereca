# #4987 — Parcours GTK « emprunter avec un autre profil »

**Statut : contrôleur opt-in, non activé automatiquement.** La PR #5158
ne collecte aucune preuve fournisseur et ne crée aucun bouton général
« Emprunter avec… ».

## Conditions avant de proposer ce parcours

1. Une **observation source fraîche** de l'opération d'emprunt pour
   l'identité et le compte prêteur exacts, avec abonnement, quota et
   permission positifs — pas déduits de la connexion ni du catalogue.
2. Une preuve indépendante **pour le livre, sa collection et son édition**
   attestant l'offre disponible au même compte.
3. Les coordonnées exactes de ces deux preuves sont liées à une session
   prêteuse vivante et au résultat affiché dans le détail Search actuel.
4. Aucun prêt déjà réclamé dans le journal durable du prêteur.
5. Le Core #5141 et sa validation du choix #5140/#5134 sont intégrés.

Le seul contrat fournisseur BnF actuellement identifié
(`BnfProviderSession.borrower_eligibility_for`) renvoie volontairement
`account_state=unknown`, `quota_state=unknown`,
`permission=unknown` et `item_offer=unknown|unavailable`.
Il **ne peut pas** produire de preuve positive complète, même si une
fiche contient un bouton Emprunter. La capacité de POST manuel BnF
ne remplace pas la preuve d'éligibilité interprofils. **Ne pas fabriquer
un `ObservedBorrowOffer` positif à partir d'un bouton HTML, du stock
catalogue, d'un ISBN ou d'une présence de compte.**

## Parcours prévu lorsque les preuves seront disponibles

1. L'utilisateur ouvre une notice liée au Search exact et choisit
   volontairement « Emprunter avec… ».
2. Core donne un aperçu 0/1/N de prêteurs **non actionnable** ;
   si 0, le contrôleur ne crée aucun dialogue.
3. L'utilisateur sélectionne **explicitement** un prêteur ; aucun
   compte n'est sélectionné à sa place.
4. Une seconde fenêtre explique qu'il s'agit d'un prêt **sur le compte
   prêteur**, et que livraison, ADEPT, copie et transfert vers le demandeur
   ne sont **pas** autorisés.
5. Juste avant et à l'intérieur du worker, le Core réévalue le snapshot
   complet, le demandeur actif, le compte/édition/session prêteur,
   les permissions, le journal durable et la source.
6. Le fournisseur reçoit une seule demande sous claim atomique ;
   un résultat incertain est conservé et ne déclenche jamais de retry.
7. Sur confirmation positive, seul le **suivi en lecture seule**
   `Prêt confirmé / Livraison indisponible / Transfert bloqué`
   peut être ajouté au détail courant.

## Validation attendue

- Annuler au choix ou à la confirmation : aucun claim ni POST.
- A→B→A, changement d'instance lecteur sans changement d'ID,
  session reconnectée, source/compte/édition/onglet remplacé :
  aucune action sur l'ancien clic.
- Deux réponses GTK simultanées ou deux appels de dialogue :
  une seule tâche au plus, puis un seul POST grâce au Core.
- Timeout, résultat non confirmé, fermeture de fenêtre :
  aucune exception/URL, identifiant de prêt ou credential privé affiché.
- Preuve d'éligibilité complète absente, fournisseur BnF inconnu,
  aucune observation fournie : aucun dialogue.
- PR CI et E2E GTK verts sur HEAD exact, intégration avec #5124 pour
  le suivi et #5141 pour l'exécution, puis recette réelle **avec
  consentement de l'utilisateur**.

Ce contrôleur n'est **pas** un collecteur d'observations : une source
positive indépendante est un blocage explicite avant l'activation.
