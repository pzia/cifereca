# P0 #2055 — NOUVEL ARBITRAGE 01/11/2026 (prioritaire)

Le lecteur vérifie **visuellement sur pret.bnf.fr** que le titre numérique est empruntable pour son compte, puis choisit dans Cifereca **« J’ai vérifié sur BnF Prêt — emprunter »**. La BnF peut encore refuser la demande (Pass, quota, disponibilité). Les quatre marqueurs automatiques d'éligibilité ne sont **plus un veto P0** ; leur qualification et leur affichage sont reportés au **P2 #4063**. Le consentement humain **n'est pas une preuve fournisseur**.

La façade Core exige `confirmed=True` ET `site_checked=True` (types exacts), puis revalide capacité du PluginManager, compte/notice/session, bail et époque, et réserve un `loan_borrow_claim_once` durable **avant l'unique POST**. Les marqueurs `BorrowerOperationEvidence` ne sont plus consultés dans cette voie manuelle. Le Core instancie un `LendingActionAdmission` consultatif **réservé à cette branche**, sans prétendre que `assess_lending_action(permission_attested=True)` disposerait d'une permission fournisseur attestée. Seule la relecture positive du prêt actif exact valide le succès ; timeout/refus/indéterminé ne déclenche **jamais un retry POST**. Le garde GTK interdit toujours `session.borrow` direct. ACSM/ADEPT et synchronisation restent séparés.

**État d'activation** : le plugin BnF 0.3.20 ne déclare pas `acquisition.borrow` ni `ProviderCapabilities.borrow`, et la publication de capacité via le contrat de prêt actuel attend encore une méthode dédiée compatible. Le changement d'admission et de dialogue **n'active aucun POST réel**. Prochaine tranche P0 : qualification de la façade de capacité sous le Core, tests anti-rejeu et recette avec compte consenti, sans reprendre les quatre marqueurs comme prérequis. P0 #2055 reste ouvert.

---

## Contrat historique initial (supplanté pour la condition de quota/Pass)

Les étapes ci-dessous sont conservées comme historique de la tranche #4044 : **toute exigence de quatre preuves positives automatiques, et tout texte affirmant leur nécessité pour débloquer le P0, sont remplacés par l'arbitrage ci-dessus**. Les gardes de scope/epoch, la capacité explicite, le claim anti-rejeu et la vérification du prêt demeurent obligatoires.

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

## Raccordement CoreUI sous confirmation dédiée (tranche suivante)

Le `LibraryService` expose `can_borrow_bnf` et `borrow_bnf_manual`
uniquement via le Core. La disponibilité UI vérifie sans I/O fournisseur :
capability réelle du PluginManager, descripteur, session et compte primaire
exact, bail et présence d'une méthode fournisseur de preuve positive.
**Elle n'est pas l'éligibilité** : le contrôleur délègue toujours l'admission
réelle à `manual_bnf_borrow` après consentement.

`gui/bnf_borrow_controller.py` construit un dialogue explicite distinct de
l'ancien `session.borrow()`. Son geste « Confirmer l'emprunt » est lié
à l'instance de compte, aux coordonnées de notice et aux générations
identité/compte/recherche ainsi qu'à l'epoch Core. Le worker revérifie
ces scellés au démarrage, puis passe `expected_account` et
`expected_context_epoch` au dispatcher Core ; toute mutation ou ABA est
refusée, sans préemption d'un claim. Une seule réponse du dialogue peut
enregistrer une tâche. Résultat positif uniquement si Core renvoie
`confirmed + provider_verified`, suivi de l'actualisation des emprunts.
Résultat inconnu : message demandant de vérifier le portail et précisant
l'absence de rejeu automatique ; aucune exception privée n'est rendue.

La visibilité du bouton générique BnF demeure **fausse** avec le manifeste
actuel. Le contrôleur BnF remplace seulement son ancien rejet lorsque le
Core validera la capability et les faits de permission ; le chemin direct
`session.borrow` demeure interdit.

## Non-activation explicite

Ce code n'est **pas** une nouvelle API d'emprunt disponible : le manifeste BnF et `capabilities.borrow` restent négatifs, `borrower_eligibility_for` manque, et aucun composant UI/sync ne l'invoque. Les tests utilisent un faux fournisseur ayant *fictivement* publié la capability et les quatre preuves métier pour tester l'orchestration ; ils ne simulent pas une autorisation BnF réelle.

Pour activer : qualification privée des signaux de souscription/quota/permission, projection fournisseur documentée et testée, décision d'exposition de `acquisition.borrow` et de `ProviderSession.borrow`, confirmation GTK explicitement liée au compte, puis recette réelle consentie. P0 #2055 reste ouvert. Aucun Chromium ni parcours automatique.

Références : #2055, #4035, #4037, #4039, #4042, #3120, #3121.
