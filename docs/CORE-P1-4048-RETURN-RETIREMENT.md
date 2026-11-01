# P1 #4048 — Retrait des restitutions volontaires (contrat de sortie)

## Décision et état

À compter de la décision du 1er novembre 2026, **Cifereca ne doit plus
déclencher de restitution volontaire** : ni retour PNB/Paris
`ReturnDigitalLoan`, ni retour de licence Adobe via ADEPT, ni retour des
autres fournisseurs, y compris par Core, GUI, broker, CLI ou plugin.

Cette décision **ne concerne pas** la lecture de l'inventaire et des états
historiques « rendu », « expiré », « clos ». Elle n'interdit pas à un
fournisseur d'expirer naturellement un prêt.

**État de migration, à vérifier avant release :**
- [#4056](https://github.com/pzia/cifereca/pull/4056) : boutons de
  restitution retirés des fiches et des facettes, fusionné.
- [#4067](https://github.com/pzia/cifereca/pull/4067) : switch
  `experimental_actions` retiré, fusionné ; la valeur ancienne ne
  constitue pas une autorisation.
- [#4059](https://github.com/pzia/cifereca/pull/4059) : retrait
  Core/Plugins en **draft**, **non livré** ; ne pas interpréter son code
  comme déjà présent sur `main`.

La clôture de #4048 est interdite sans fusion des contrats mutationnels
et les trois validations CI du commit exact.

## Inventaire des frontières d'exécution à éliminer

| Couche | Entrée historique | Sortie exigée |
| --- | --- | --- |
| GUI | `confirm_return`, `_return_loan`, `_return_loan_pnb`, `return_adept_from_detail` | Absence de commande et de tâche |
| Broker ADEPT | `AdeptLoanBrokerImpl.return_acquisition` | Aucun appel service |
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
