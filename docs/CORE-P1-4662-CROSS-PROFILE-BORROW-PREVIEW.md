# P1 #4662 — aperçu des comptes prêteurs multi-profils

## Tranche livrée — sélection strictement non mutante

`borrow_for_identity_preview.py` propose un aperçu typé par **édition exacte**
pour la personne en consultation (`requesting_identity_id`). Les candidats
fournissent un compte prêteur `Account`, une notice `SearchResult`,
`LendingOperationFact` et `BorrowerOperationEligibility` **déjà vérifiés par
le Core**, ainsi que l'epoch de contexte, l'epoch de session, un horodatage
borné et l'identifiant d'édition réclamé. L'éligibilité
`eligible_observed` ne vaut **jamais** consentement ni autorisation de POST.
Un résultat positif conserve désormais **l'identité, le fournisseur, le
credential et le compte évalués** ; l'aperçu refuse une permission pourtant
« éligible » si elle concerne un autre emprunteur de la même branche.
Les identifiants sont internes, absents des libellés et du `repr`.

La projection donne quatre états UX :

| Choix qualifiés | Statut | Libellé |
| --- | --- | --- |
| Aucun | `none` | Emprunt non vérifié |
| Un compte du profil actif | `own` | Emprunter |
| Un seul autre profil | `external` | Emprunter avec « nom du profil » |
| Plusieurs comptes/profils | `choose` | Emprunter avec… |

Deux comptes d'un même profil sont des options distinctes, épinglées à leur
credential/account, docbase, notice exacte, édition et epoch. Le plan
`BorrowForIdentityPreview` garde `executable=False`, et l'identifiant
opaque `option_id` **n'est pas** un jeton d'action : il est révoqué lorsque
l'epoch de contexte change. Le tri des choix suit toutefois les coordonnées
de source **stables**, jamais cet identifiant opaque renouvelé, pour ne pas
intervertir les ordinales « choix 1/2 » entre deux comptes après A→B→A.
Les credentials et comptes ne sont jamais dans les libellés affichés.

Tout candidat périmé (plus de 300 secondes), venant d'une autre édition,
sans capacité de prêt publiée et session prête, avec observation de permission
incomplète ou compte non principal est refusé. Les doublons exacts n'ajoutent
pas d'options ; des états contradictoires pour un même compte et une même
notice refusent le snapshot plutôt que d'élire la première liste. Aucune
recherche fournisseur, lecture SQLite, transaction, download ou transfert.

## Frontière de sécurité

**Ce module ne vérifie pas directement la propriété SQLite de session ni la
provenance réseau.** Seul le futur orchestrateur Core (jamais le plugin
Recommandations) doit lui présenter des preuves **après** capture et
revérification d'un lease Core/compte réel, des epochs et des propriétés de
l'offre exacte. On ne peut pas substituer une simple fiche catalogue à une
observation de permission du prêteur.

Au clic ultérieur, le Core doit recommencer l'admission à partir des
coordonnées exactes et non pas se fier à l'option opaque : compte prêteur
toujours actif, droit/restriction, changement A→B→A, anti-rejeu persistant,
confirmation explicite et POST sous **l'identité prêteuse** seulement.
`manual_borrow` et son checkpoint restent les points d'entrée uniques.

Le transfert A→B appartient **à une étape indépendante** après constat
positif du prêt puis livraison exploitable sous A. Le champ
`transfer_state=unknown|blocked|admissible_unverified` n'est jamais un droit
de copie, une licence ou la preuve de disponibilité d'un fichier déchiffré.
L'usage inter-profils bloqué n'autorise pas de contournement de DRM ; le prêt
confirmé chez A ne doit pas être rejoué en cas de transfert impossible.

## À faire

Brancher un résolveur d'offres réel, scope/epoch Core vérifiés, les deux
surfaces Recherche et Recommandations via une **API publique** et une action
distincte de sélection/confirmation. Valider le résultat du fournisseur,
la sauvegarde de l'étape 1 et l'appel au transfert existant sous droits
attestés. Recette GTK 1366/1600 et double clic/rebind/crash. Cette tranche
n'introduit aucun appel utilisateur ni aucune mutation ; elle ne clôt pas #4662.
