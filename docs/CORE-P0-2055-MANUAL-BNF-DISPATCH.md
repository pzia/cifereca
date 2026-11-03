# BnF Prêt #2055/#3121 — emprunt manuel en un clic

## Décision produit du 03/11/2026 (remplace la confirmation du 01/11)

Le clic volontaire sur **« Emprunter »** dans la fiche exacte suffit à
demander un nouveau prêt. **Aucun second dialogue et aucune attestation
« J’ai vérifié sur BnF »** ne sont exigés. Cela ne constitue pas une preuve
de droit ou de disponibilité : le fournisseur reste décisionnaire.

Cifereca n'impose **pas** un veto sur quatre marqueurs non qualifiés :
Pass actif, quota restant, exemplaires positifs, permission générale.
La BnF est l'autorité métier qui peut refuser la transaction.
L'affichage informatif de ces marqueurs relève désormais de
[**#4063 P2**](https://github.com/pzia/cifereca/issues/4063).
Le champ `BorrowerOperationEvidence` et `assess_borrower_eligibility`
restent disponibles pour d'autres sources et pour l'observation BnF
read-only ; ils ne sont PAS utilisés comme admission de ce parcours précis.

## Chemin d'exécution réservé à BnF Prêt

Le détail de notice affiche la **section d'emprunt BnF** même si le
libellé catalogue est `Inconnue`, `Indisponible` ou vide ; cela ne rend
pas le bouton actif pour autant. Celui-ci dépend exclusivement de
`can_borrow_bnf` (capacité, session, compte et claim), puis du geste
humain de clic sur l'action exacte « Emprunter ». 
Les autres prêteurs conservent leur condition d'affichage
`availability == "Empruntable"` et les stores commerciaux restent
exclus. Tests purs dans
`tests/test_core_p0_2055_bnf_detail_action_visibility.py`.


1. `MainWindow.can_borrow_item` consulte
   `service.can_borrow_bnf(item)` : plugin installé/activé, capacité
   `acquisition.borrow` effectivement publiée, session connectée,
   compte primaire unique de l'identité exacte, notice numérique BNF_PRET,
   bail Core valide et aucune tentative antérieure enregistrée.
   **Aucune requête HTTP pour afficher le bouton**. L'état d'un
   livre pris sur le portail n'est pas déduit du résultat catalogue.
2. `gui/bnf_borrow_controller.py` reçoit **uniquement le clic explicite**
   sur la notice sélectionnée et soumet une tâche unique, sans dialogue.
   Un deuxième clic avant la fin de la tâche ne soumet rien.
   Le geste est figé sur l'instance du compte, la notice, les générations
   d'identité et de sélection, la session et les epochs. Toute mutation
   de contexte invalide la tâche avant POST.
3. `service.borrow_bnf_manual` appelle **uniquement**
   `core.manual_bnf_borrow`. Seul `confirmed is True` atteste la demande
   explicite ; le paramètre historique `site_checked` est toléré pour
   compatibilité mais **ignoré**, sans être envoyé par la GUI.
   La demande est limitée à
   `source_provider=bnf-pret` / `docbase=BNF_PRET` / `medium=digital`
   / même compte primaire. Le Core recontrôle l'identité, la
   capability réelle du PluginManager et de la session et le
   `BorrowActionScopeLease`.
4. L'admission `LendingActionAdmission(loan.borrow, manual, admitted)`
   est **spécifique à une tentative demandée par l'utilisateur sur BnF**,
   pas une déclaration de droit positif. Ne pas appeler
   `assess_lending_action(permission_attested=True)` : ce contrat
   générique exige une preuve d'entitlement *machine*, que la simple
   action utilisateur ne fournit pas. Aucune ouverture ni
   synchronisation planifiée ne peut obtenir cette admission.
5. Le Core réserve atomiquement **un seul**
   `Storage.loan_borrow_claim_once` par identité/compte/édition.
   Toute réservation existante (même résultat incertain/corrompu)
   interdit un nouveau POST automatique ; un bail devenu obsolète
   avant POST consomme le claim mais ne contacte pas le prêteur.
6. Sous le verrou du provider, `borrow_explicit_with_claim` lit
   l'activité fraîche et refuse un prêt exact déjà positif ; GET
   fiche du livre sélectionné ; vérifie un lien d'emprunt unique non
   désactivé ; un badge générique `.lb_unavailable` coexistant avec
   ce lien est un marqueur informatif P2 #4063 et **ne bloque pas**
   l'essai manuel ; GET de confirmation puis
   extraction du formulaire POST et CSRF liés à la même édition.
   **Un seul POST**, sans redirection ni retry. La BnF peut répondre
   par refus, erreur ou succès non prouvé.
7. L'activité BnF est relue **indépendamment** et le prêt actif
   exact est corrélé au reçu ; le Core revalide toutes les coordonnées
   du `Loan` puis clôt le claim SQLite. `confirmed` exige
   `provider_verified=True` ; HTTP 200 et présence du formulaire
   ne suffisent jamais.
8. Si une erreur se produit **avant tout accès au transport POST**
   (GET activité, fiche ou formulaire impossible), le provider émet
   `BorrowRequestNotSubmitted`. Le Core appelle
   `Storage.loan_borrow_release_unsent`, **CAS atomique** limité
   à l'opération exacte et à l'état `request_sent/not_checked`.
   Le retour `borrow_not_submitted` n'affirme aucun droit et
   ne provoque aucun retry automatique ; un nouvel essai exige
   un nouveau clic humain sur la fiche exacte.
9. **Une fois le transport POST invoqué**, le provider ne peut plus
   émettre `BorrowRequestNotSubmitted` ; timeout, refus 4xx/5xx,
   réponse ambiguë, contexte modifié ou inventaire incomplet
   consomment durablement le claim (`verification_unavailable`).
   Toute répétition automatique est interdite : l'utilisateur
   vérifie Mes emprunts / le portail BnF avant toute suite.
   Le téléchargement ACSM/ADEPT est une opération distincte ; aucune
   licence ni acquisition implicite pendant le nouvel emprunt.

## Protection de la voie générique

La capability BnF `ProviderCapabilities.borrow=True` et le manifeste
`acquisition.borrow` rendent **l'action dédiée « Emprunter »** visible
pour une session admissible. La méthode legacy `BnfProviderSession.borrow`
refuse toujours par `bnf_borrow_requires_core_dispatch` sans aucun I/O.
`MainWindow.borrow_search_item` intercepte BnF AVANT l'ancien
`session.borrow` et ne le rejoint jamais.

Les autres fournisseurs ne sont pas concernés par cette exception produit
manuelle ; leurs preuves de droits et leurs contrats d'admission ne sont
pas assouplis.

## Recette & limites

Les tests du parcours utilisent des données entièrement synthétiques,
`httpx.MockTransport`, SQLite réelle Core, garde GTK headless et les
contrats PR CI / installation rollback. Les PR précédentes #4037, #4039,
#4042, #4044, #4052, #4057, #4061 apportent les briques de sécurité.

**La publication dans main ne vaut pas une recette réelle :** après
déploiement, une tentative volontaire sur une notice effectivement
empruntable devra vérifier le comportement terrain de la BnF, le résultat
dans Mes emprunts et, séparément, la livraison ACSM/ADEPT. N'inclure
aucun cookie, nom de compte, HTML privé, URL signée ou licence dans GitHub.
#2055 reste ouvert jusqu'à recette ; #4063 traite les marqueurs de quota.
