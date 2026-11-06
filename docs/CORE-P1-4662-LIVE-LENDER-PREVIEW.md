# P1 #4662 — projection Core « prêteur réel » (tranche incrémentale)

## Frontière

`LibraryService.preview_borrow_for_current_identity(expected_edition_id=..., observations=..., now=...)`
expose désormais le preview 0/1/N existant à partir de sessions/comptes **enregistrés dans le Core et SQLite**. Les entrées `ObservedBorrowOffer` contiennent une notice d'édition exacte, deux preuves d'emprunteur **préalablement observées** — `lending_capabilities.BorrowerOperationEvidence` pour la branche et le compte, et `lending_borrower_eligibility.BorrowerOperationEvidence` dans `edition_evidence` pour l'offre exacte (collection/record_id) —, leur date d'observation et le statut prudent du transfert (`unknown` par défaut).

**Aucune découverte de prêteur, connexion distante, recherche réseau, vérification HTTP, emprunt, import ADEPT ni copie n'est effectuée par le preview.** Une simple notice Paris `CanLoan`, un quota inconnu, la connexion d'une carte ou un score Recommandations ne fabriquent jamais un témoin éligible. Sans preuve positive reliée au bon `identity_id / provider / credential / account / docbase / operation`, le résultat est `none`.

## Vérification locale

Avant de créer une option, le Core recoupe :

- identité prêteuse connue, propriété explicite de la connexion SQLite, connexion activée et session vivante dans le registre exact ;
- compte primaire présent **une fois** dans le Core et dans la session d'origine, même objet, sans appel au getter fournisseur `accounts` ;
- compte Paris principal sélectionné **chez le prêteur** (`_normal_paris_source_current`), sans activation du profil prêteur ni fallback sur le compte du demandeur ;
- déclaration `acquisition.borrow`, méthode et capability réelle selon `assess_lending_operation`, puis observation personnelle selon `assess_borrower_operation_eligibility` ;
- absence de claim antérieur sur l'édition/collection/compte, cohérence des epochs de contexte et session, ainsi que de l'identité demandeuse au début et à la fin.

La seule sortie est `BorrowForIdentityPreview` avec `executable=False` et des `option_id` opaques ; aucune donnée de credential, barcode ou connexion n'entre dans les libellés. Une indication `transfer_state=blocked` peut être affichée mais jamais convertie en permission de copie. `admissible_unverified` n'est **pas** émis par cette tranche : aucune politique de droits/licences n'a encore été attestée. Les collisions ou faits ambigus sont refusés.

## Ce que cette PR ne fait pas

- Il n'existe **aucun producteur live autonome d'une observation fournisseur positive** ; seuls les fournisseurs et workflows disposant déjà d'une preuve personnelle précollectée pourront alimenter ce nouvel adaptateur. Cette API n'invente donc pas un bouton « Emprunter avec » chez un utilisateur réel.
- Aucun callback CoreUI mutateur ni menu Recommandations n'est livré ici.
- L'exécution d'un prêt inter-profils et la copie ne doivent être ajoutées qu'avec un nouveau contrat de sélection et de revalidation **autoritative au clic**, le claim normal `at-most-once` et la vérification des droits de transfert. Pas de réutilisation directe du `option_id` comme autorisation.

## Recette locale

`tests/test_core_p1_4662_live_lender_preview.py` couvre 0/1/N, deux collections distinctes, session B utilisée depuis A sans repli, désactivation/rebind compte, changement de compte primaire, claim consommé, capability absente, quota/abonnement/permission refusés ou inconnus, et deux epochs A→B→A. Le faux fournisseur interdit explicitement `connect/search/borrow` ; aucun réseau ni mutation n'est attendu.

Cette tranche prépare #4662 sans élargir la clôture de #4673, sans créer le parcours #3120 P2 et sans changer l'admission mutatrice de #3121.

## Revalidation d'un choix explicite au clic — tranche complémentaire

`LibraryService.revalidate_borrow_lender_choice(captured_preview, selected_option_id, expected_edition_id, observations, now)` ne reçoit **jamais** un prêteur par défaut. Elle exige un `option_id` issu du preview affiché, vérifie qu'il désigne **une seule** option exacte, recalcule un preview Core/SQLite frais et exige une égalité complète de la sélection (coordonnées, époque, libellé et état de transfert). Les cas A→B→A, déconnexion, remplacement de compte, claim déjà consommé, révision du fournisseur ou observation devenue vieille retournent `BorrowLenderSelection(status="revoked", reason_code="selection_stale", executable=False)`.

Un choix toujours à jour retourne `status="current", reason_code="selection_current_unverified"`, **jamais** une autorisation de prêt ni une capacité de transférer. Aucun compte n'est activé ; aucune sélection d'une autre option n'est faite silencieusement ; aucune transaction n'est possible par cette API. La future action de confirmation doit garder son propre consentement, le claim durable et les guards du fournisseur, sans jamais interpréter ce résultat comme une admission mutatrice suffisante.

La portée de l'option inclut maintenant l'**époque globale de source Core** et l'**époque locale de session** (combinaison injective non réversible en libellé) : un reconnect qui reproduit les mêmes credentials et le même compteur local ne rend pas valide le token d'avant déconnexion. Régression spécifique de session remplacée à epoch identique.

## Fermeture de l'écart « compte positif, édition non attestée »

Le verdict positif par `lending_capabilities.assess_borrower_operation_eligibility` ne qualifie que le **compte et sa branche**, pas un ouvrage précis. Le preview exige **en plus** `assess_borrower_eligibility(capability, account, notice, edition_evidence).status == "eligible"` pour le **même record_id, docbase et source_collection**, ainsi que le même compte et credential.

L'argument `edition_evidence` est facultatif dans la signature pour préserver les clients du DTO ; absent, incomplet, mal qualifié, sur une autre édition/collection, ou attestant un refus, il donne **zéro option**. Une preuve de compte A sous DILICOM ne permet plus de proposer un livre B de DILICOM. La revalidation du clic recalcule ces deux évaluations : une offre qui devient inconnue ou indisponible est révoquée.

Les deux structures portent le même nom `BorrowerOperationEvidence`, mais **leurs contrats sont différents** ; elles sont vérifiées chacune par le validateur public correspondant. Les champs ne sont jamais synthétisés à partir de `CanLoan`, d'un titre, du succès de connexion ou de l'inventaire des prêts. Même un double positif demeure consultatif (`executable=False`) et n'autorise jamais la copie vers un autre profil.

## Observations contradictoires d'une offre exacte

Le producteur peut recevoir deux lectures concurrentes : l'une annonce une
offre admissible et l'autre la marque indisponible, inconnue, quota épuisé,
ou transfert restreint. Filtrer seulement les témoins négatifs puis conserver
un témoin positif plus ancien **fabriquerait un faux choix actif**.

Avant tout calcul de preview, `_noncontradictory_observations` regroupe les
observations par `identity/provider/credential/account/docbase/record_id/
collection/edition`. Des doublons **identiques** (mêmes preuves, horodatage,
état de transfert et support numérique) sont dédupliqués ; des observations
distinctes de la même route sont conservativement **retirées toutes**.
L'ambiguïté affecte cette offre, pas les autres comptes ni collections.
Le moteur n'effectue aucune préférence arbitraire par ordre de liste,
fraîcheur présumée ou statut positif. Le callback de #4810 réévalue la même
règle et révoque un choix ancien devenu contradictoire.

Une preuve d'un autre compte/emprunteur n'est pas une négation du prêteur
courant. Des entrées sans identité de prêt suffisamment portée restent
non admissibles ; elles n'autorisent ni interdiction globale ni offre positive.
Aucun nouveau flux de récupération de données fournisseur n'est ajouté.

Une observation **négative par édition seulement** (sans bilan complet du compte)
est aussi reliée à la bonne route depuis les coordonnées
`edition_evidence.identity_id/provider_id/credential_id/borrower_id/
docbase/record_id/source_collection` : un refus fournisseur précis ne
disparaît pas simplement parce que les données de quota ou d'abonnement
sont inconnues. Un refus relatif à un autre emprunteur ou à un autre livre
ne peut en revanche invalider la permission du prêteur exact.

## Révocation lors du renouvellement d'une preuve personnelle

À coordonnées de compte, édition, collection et epochs de session **identiques**,
un nouveau constat positif peut remplacer l'ancien. Le `option_id` du preview
incorpore désormais `observed_at` dans son SHA-256 opaque. Le clic conservé
avant un refresh ne peut plus se présenter comme le choix sur le constat
nouvellement observé ; `revalidate_borrow_lender_choice` renvoie
`selection_stale`. Le même constat non renouvelé garde un identifiant stable.

Cette révocation ne transforme pas l'âge d'observation en autorisation
fournisseur et ne modifie ni les claims anti-rejeu, ni la politique de transfert.
Le fournisseur doit toujours attester les deux preuves positives par compte
et édition ; aucune requête supplémentaire n'est effectuée à l'affichage.
