# Core P0 #3171 — bail d'emprunteur de la session de livraison

Le moteur Sync canonique validait déjà le compte enregistré dans le Core,
le credential actif SQLite, le registre de sessions, l'époque et le consentement
persisté. Il ne comparait pas **le compte upstream actuellement exposé par la
session connectée** avec le SourceKey de l'offre. Une session peut changer de
compte sans changer d'objet Python ni incrémenter l'époque du Core.

`convergence_execution.current_memory()` exige maintenant exactement un
`Account` correspondant à (identité, fournisseur, credential, compte)
dans `session.accounts`. Un compte absent, changé, dupliqué ou une collection
malformée invalide le bail. Le contrôle est réexécuté avant GET, après GET
et dans les vérifications contextuelles qui encadrent la transaction
Artifact / Placement / SourceReference / OriginLink.

## Preuves synthétiques

- `tests/test_core_p0_3171_convergence_execution.py` : quatre refus avant
  GET (emprunteur remplacé, absent, doublonné, réponse malformée) ;
  remplacement **pendant** GET -> aucun lien ni Placement canonique publié.
- Les fixtures de Sync et de reprise de réception terminée portent
  explicitement les comptes de session et restent compatibles avec deux
  providers ayant des droits distincts sur le même SHA.
- Toute la CI Core/Plugins et le cold-install doivent passer avant fusion.

## Limites

Aucun téléchargement fournisseur ni activation ADEPT réelle. Ce garde ne
remplace ni les contrôles d'offre exacte du plugin #3252, ni le consentement
persistant, ni l'exécution d'une livraison Core complète. #3171 reste ouvert.
