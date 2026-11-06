# P1 #4662 — projection Core « prêteur réel » (tranche incrémentale)

## Frontière

`LibraryService.preview_borrow_for_current_identity(expected_edition_id=..., observations=..., now=...)`
expose désormais le preview 0/1/N existant à partir de sessions/comptes **enregistrés dans le Core et SQLite**. Les entrées `ObservedBorrowOffer` contiennent une notice d'édition exacte, la preuve d'emprunteur **préalablement observée** `BorrowerOperationEvidence`, sa date et le statut prudent du transfert (`unknown` par défaut).

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
