# #4987 — Intégration contrôlée du prêt interprofils (P1)

**État : candidat d'intégration, pas de fusion ni d'activation utilisateur.**
La branche de validation `focus/p1-4987-integrated-choice-lender-executor-20261108`
et la PR #5151 sont **empilées sur la branche de #5141**, pas sur `main`.

## Contrats et ordre de fusion

1. **#5140** — Unicité du binding du prêteur et prise en charge des epochs
   de session fournisseur entiers bornés ou opaques bornés. Condition préalable
   pour qu'une session BnF à epoch UUID soit proposable ; jamais exposer le
   token dans l'UI.
2. **#5134** — Le clic est lié à **l'intégralité du snapshot 0/1/N**
   (y compris les propositions refusées), pas seulement au prêteur choisi.
   Même fichier `borrow_for_identity_live.py` et même fichier de tests
   que #5140 : **résoudre explicitement les changements dans les deux**,
   ne pas conserver seulement l'un des deux hunks.
3. **#5141** — Bail Core demandeur/prêteur, confirmation explicite, claim
   `loan.borrow.v1` durable **avant** le callback fournisseur
   `borrow_explicit_with_claim`. Un résultat non attesté laisse le claim
   consommé et ne réessaie pas ; seul `BorrowRequestNotSubmitted` attesté
   peut libérer sa propre réservation. Garder `current_identity` du demandeur.
   Rebaser/réexécuter après intégration des deux PR précédentes.
4. **#5132** — Projection read-only du checkpoint du prêteur sous contexte
   demandeur épinglé : prêt confirmé ne signifie ni livraison ni transfert.
5. **#5124** — Panneau GTK **en lecture seule** : prêt/livraison/transfert
   distincts ; la méthode doit encore être branchée au parcours réel.
6. **#5127** — Plan read-only alternatif : modifie aussi `services.py`.
   Examiner la compatibilité avec la façade directe #5141 avant de fusionner,
   pour éviter deux sources d'autorité ou un doublon de façade.

## Matrice de validation minimale

- `pytest -q tests/test_core_p1_4662_live_lender_preview.py`
  doit conserver toutes les protections 0/1/N et epoch opaque.
- `pytest -q tests/test_core_p1_4987_cross_profile_action_scope.py
  tests/test_core_p1_4987_cross_profile_executor.py`
  doit garantir un seul claim, source et session exactes, consentement,
  suppression des retours privés après rebind et absence de transfert.
- `pytest -q tests/test_core_p1_4987_cross_profile_paris_http.py
  tests/test_core_p1_4987_composed_bnf_lender.py
  tests/test_core_p1_4987_composed_bnf_http.py`
  : vrais adaptateurs Paris/BnF via HTTP synthétique ou session BnF synthétique ;
  aucun compte réel, aucune livraison.
- Valider **PR CI, installation Core et (si UI touchée) E2E GTK** sur le
  **HEAD de la composition** ; le succès des PR isolées ne suffit pas.
- Vérifier la relecture après redémarrage SQLite, le refus du double claim
  par une autre connexion et le traitement du résultat HTTP indéterminé.
- Vérifier l'intégrabilité avec `main` au moment du merge : des branches
  agent peuvent avoir modifié les mêmes services pendant ce travail.

## Interdits tant que #4987 reste ouvert

- Ne jamais changer temporairement le profil actif pour agir sur le compte
  prêteur : épingler le demandeur, le prêteur, la session et la notice.
- Ne pas traiter une éligibilité catalogue ou une observation de quota comme
  autorisation de mutation distante ; le fournisseur revalide les droits.
- Ne jamais conclure qu'un ACSM, un transfert entre profils ou un import
  ADEPT est permis du seul fait qu'un prêt est confirmé.
- Ne jamais relancer un POST pour corriger une livraison, une erreur de
  parseur, un timeout, un échec d'UI ou un checkpoint illisible.
- Ne pas activer un bouton GTK tant que l'origine des
  `ObservedBorrowOffer` n'est pas prouvée fraîche et que le parcours
  0/1/N + confirmation + observation de résultat n'est pas intégré.
- Ne pas fusionner cette **PR de test empilée** comme une alternative aux
  PR sources. Après les merges ordonnés, reporter ses tests nouveaux et
  supprimer la duplication temporaire des hunks.

**Recette réelle Paris/BnF, UX, droits de livraison et transfert demeurent
ouverts.**
