# P1 #4048 — Retrait des restitutions volontaires (contrat de sortie)

## Décision et état

À compter de la décision du 1er novembre 2026, **Cifereca ne doit plus
déclencher de restitution volontaire** : ni retour PNB/Paris
`ReturnDigitalLoan`, ni retour de licence Adobe via ADEPT, ni retour des
autres fournisseurs, y compris par Core, GUI, broker, CLI ou plugin.

Cette décision **ne concerne pas** la lecture de l'inventaire et des états
historiques « rendu », « expiré », « clos ». Elle n'interdit pas à un
fournisseur d'expirer naturellement un prêt.

**État fonctionnel au 03/11/2026 : terminé, [#4048](https://github.com/pzia/cifereca/issues/4048) fermé.**

Le retrait a été livré par tranches ciblées, sans intégrer la PR brouillon
[#4059](https://github.com/pzia/cifereca/pull/4059) (102 fichiers, gelée) :

- GUI : [#4056](https://github.com/pzia/cifereca/pull/4056)
  et [#4181](https://github.com/pzia/cifereca/pull/4181) suppriment les actions,
  façades et callbacks de restitution ; le switch
  `experimental_actions` a été retiré par
  [#4067](https://github.com/pzia/cifereca/pull/4067).
- Paris PNB : [#4200](https://github.com/pzia/cifereca/pull/4200) retire
  `return_digital_loan`, la route `ReturnDigitalLoan` et la capacité déclarée.
- ADEPT : [#4210](https://github.com/pzia/cifereca/pull/4210) retire le
  broker public, [#4214](https://github.com/pzia/cifereca/pull/4214) les
  commandes dédiées de restitution, et
  [#4222](https://github.com/pzia/cifereca/pull/4222) interdit également
  `run_json(["acsm", "return", ...])` avant tout sous-processus.
- Core : [#4319](https://github.com/pzia/cifereca/pull/4319) refuse
  `execute_drm_mutation("return")` avant résolution d'un plugin ;
  [#4344](https://github.com/pzia/cifereca/pull/4344), squash
  `68333c04b509c09fa2746835a9930f5a5388bac7`, neutralise
  `return_digital`, `return_loan_prefer_adept`,
  `return_adept_acquisition`, leurs alias privés et les helpers
  mutateurs directs à la première instruction, sans claim ni I/O.

**Validation du lot Core terminal (#4344, HEAD
`eab02c318f768ecd036a5cad92ed637e5d3c2b78`) :**
[PR CI Core + Plugins](https://github.com/pzia/cifereca/actions/runs/41691133545)
SUCCESS et [installation/rollback](https://github.com/pzia/cifereca/actions/runs/41691133569)
SUCCESS. Les actions GTK avaient déjà été testées dans les PR CoreUI
antérieures (dont #4181, E2E GTK SUCCESS) ; **aucune nouvelle exécution E2E
GTK du commit #4344 n'est revendiquée**, car son diff ne modifie pas l'IHM.

Les anciennes clés `return_available` et les receipts de restitution dans
la **provenance historique** ADEPT restent lisibles ; ils ne réactivent ni
bouton, ni capability publique ni voie de mutation. L'observation des prêts
expirés/rendus, la réconciliation read-only et le fulfillment ADEPT demeurent
distincts des retours volontaires supprimés.

La clôture de #4048 est **fonctionnelle et non destructive** ; une
prochaine évolution de schéma ou un nettoyage de libellés inertes ne doit
jamais réintroduire une possibilité de retour.

## Inventaire des frontières d'exécution à éliminer

| Couche | Entrée historique | Sortie exigée |
| --- | --- | --- |
| GUI | `confirm_return`, `_return_loan`, `_return_loan_pnb`, `return_adept_from_detail` | Absence de commande et de tâche |
| Broker ADEPT | `AdeptLoanBrokerImpl.return_acquisition` | **Retiré du protocole public et de l'implémentation** (#4048) ; rapprochement, choix et rattachement préservés |
| Core | `return_digital`, `return_loan_prefer_adept`, `return_adept_acquisition` | Pas de verrou, claim, lecture ou mutation |
| Reprise | `verify_loan_return` après ancien checkpoint | Ne **jamais** relancer le retour |
| DRM | `execute_drm_mutation(operation="return")` | Refus avant découverte de plugin |
| Paris | `ParisProviderSession.return_digital`, `ParisClient.return_digital_loan` | Aucun POST `ReturnDigitalLoan` |
| ADEPT | `KoboAdeptRuntime.return_loan`, `execute(return)` | Aucune CLI de retour |
| Manifestes | `loan.return`, `lending.return` | Aucune capacité publiée |

Les contrôles doivent rester valides avec une **ancienne installation de
plugin** ou un appel Python direct à une façade héritée. Un résultat
`loan_return_removed` doit être constant et exempt de données privées.

## Préservation stricte

- Conserver SQLite, checkpoints, journal, révisions, états retournés et
  expirés, pièces jointes et inventaires de prêts historiques : lecture
  seule, aucune migration destructive.
- Conserver `Artifact`, `Placement`, `OriginLink`, profils ADEPT,
  provenance du fulfillment, identité, credential et métadonnées de
  licence, sans réacquérir ni supprimer de fichier.
- Conserver l'ACSM, les opérations ADEPT de fulfillment/import et la
  vérification d'un inventaire fournisseur exact, sans inférer un retour
  de l'absence observée dans un inventaire partiel.
- Ne pas modifier la **prolongation physique Paris** : #4047 est son
  contrat indépendant, et ne constitue pas une restitution alternative.

## Migration de la preuve de non-régression

Les anciens tests positifs #982/#984/#1120/#2975 ne sont plus un oracle
de produit après cette décision. **Ne pas les contourner par `skip` ou
`xfail` globaux**. Les remplacer par deux suites explicites :

1. **Aucune mutation** : appeler chaque ancienne route, y compris
   sans GUI, avec une source synthétique dont tout accès à session,
   stockage, inventaire, CLI, POST ou fichiers provoquerait un échec.
   Exiger un refus fermé et aucun changement de données, compte,
   identité, journal ou checkpoint.
2. **Lecture et conservation** : sur l'inventaire synthétique typé,
   vérifier prêt actif/clos/expiré, sources et branches distinctes,
   auteur exact du prêt, erreurs sans fuite privée, absence non attestée
   sur snapshot partiel, historique SQLite et artefacts inchangés.

Les tests de conformité du prêt **`loan.borrow`, `loan.renew`,
`loan.download_token`** doivent rester actifs. Vérifier la portée
source/identité au Core ; ne pas supprimer leurs barrières en retirant
`loan.return`.

## Gates de livraison

CI Core et plugins (y compris tests négatifs), test de packaging,
installation puis rollback avec plugins anciens, et E2E GTK sans bouton
de restitution. Recette hors ligne avec identité principale et compte
lié, prêts Paris physiques et numériques, provenance ADEPT ancienne,
restart et Sync activée/désactivée. Aucun appel live de restitution ne
doit être effectué pour qualifier le retrait.

Références : [#4048](https://github.com/pzia/cifereca/issues/4048),
[#3118](https://github.com/pzia/cifereca/issues/3118),
[#3121](https://github.com/pzia/cifereca/issues/3121).
