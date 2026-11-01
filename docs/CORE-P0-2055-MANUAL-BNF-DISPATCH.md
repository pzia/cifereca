# Core P0 #2055 — dispatch manuel BnF sous preuves positives

Cette tranche ajoute `cifereca.core.manual_bnf_borrow.manual_bnf_borrow(service, item, *, expected_identity_id, confirmed)` sans brancher encore le bouton GTK ni déclarer `acquisition.borrow`.

## Admission Core (en ordre)

1. Exiger `confirmed is True`, l'origine manuelle, une notice BnF numérique et l'identité attendue.
2. Résoudre depuis le **PluginManager réel** la capability `acquisition.borrow` et la confronter au descripteur et à la méthode `ProviderSession.borrow` par le contrat `assess_lending_operation`. Le manifeste BnF actuel n'en dispose pas : résultat **`unsupported`** avant tout GET.
3. Sélectionner un seul compte enregistré de l'identité exacte et capturer le bail `BorrowActionScopeLease`, y compris le compte objet, la notice, le credential, le binding persisté et l'époque Core/provider.
4. Exiger un adaptateur fournisseur `borrower_eligibility_for(item, account)` produisant une **preuve fraîche** `BorrowerOperationEvidence` : souscription active, quota disponible, offre ouvrage disponible et permission explicite. Elle doit correspondre exactement à l'identité, au compte, au catalogue et à la notice. **Cet adaptateur n'est pas encore fourni par BnF** ; un bouton ou un formulaire CSRF ne constitue pas la preuve.
5. Faire l'admission canonique `assess_borrower_eligibility` puis `assess_lending_action`, revalider le bail, et réserver `Storage.loan_borrow_claim_once` atomiquement avant le POST (une édition et un emprunteur, aucun rejeu ou remise à zéro sur un inventaire partiel).
6. Appeler l'exécuteur BnF dormant `borrow_explicit_with_claim` avec le reçu du vrai stockage et un callback de revalidation Core ; cette méthode réalise un seul POST et vérifie indépendamment l'activité positive. Vérifier à nouveau le DTO de prêt exact, puis clore le reçu SQLite par CAS.
7. Un timeout, un changement d'identité/époque, une réponse ambiguë ou une panne après claim renvoie seulement un code fermé `verification_unavailable` ; la répétition du geste utilisateur ne reposte pas. Une réponse positive ne déclenche **aucune acquisition automatique de jeton ACSM/ADEPT** ni écriture implicite de droit.

Le résultat public est un petit dictionnaire `status/reason_code/provider_verified/retry`, sans jeton, lien, nom de compte, notice ou exception fournisseur.

## Verrou CoreUI sur la voie générique

Le chemin historique `MainWindow.borrow_search_item()` invoque directement
`ProviderSession.borrow(item, account)`, sans bail, admission ou claim Core.
Pour BnF uniquement, `can_borrow_item` cache donc le bouton générique et
`borrow_search_item` refuse explicitement tout appel direct, **même si**
un manifeste/descripteur BnF publiait accidentellement `borrow=True`.
Ce garde doit rester jusqu'à l'ajout d'une vraie confirmation GTK BnF
aboutissant exclusivement au dispatcher Core vérifié. Les autres sources
conservent leur comportement historique.

## Non-activation explicite

Ce code n'est **pas** une nouvelle API d'emprunt disponible : le manifeste BnF et `capabilities.borrow` restent négatifs, `borrower_eligibility_for` manque, et aucun composant UI/sync ne l'invoque. Les tests utilisent un faux fournisseur ayant *fictivement* publié la capability et les quatre preuves métier pour tester l'orchestration ; ils ne simulent pas une autorisation BnF réelle.

Pour activer : qualification privée des signaux de souscription/quota/permission, projection fournisseur documentée et testée, décision d'exposition de `acquisition.borrow` et de `ProviderSession.borrow`, confirmation GTK explicitement liée au compte, puis recette réelle consentie. P0 #2055 reste ouvert. Aucun Chromium ni parcours automatique.

Références : #2055, #4035, #4037, #4039, #4042, #3120, #3121.
