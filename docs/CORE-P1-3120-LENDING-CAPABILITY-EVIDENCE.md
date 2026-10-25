# P1 #3120 — capacités de prêt déclarées vs disponibilité

**Premier lot additif, lecture seule** dans
`cifereca.plugin_api.lending_capabilities`. Aucune modification des
manifestes Paris/BnF, des providers, de l'UI ni des garde-fous existants.

`assess_lending_operation(operation, manifest_capabilities, descriptor,
session, account)` distingue exactement :

1. `manifest_declared` : présence de la capability canonique ; la liste
   manquante, malformée ou dupliquée ne signifie jamais « tout supporté ».
2. `descriptor_declared` : booléen `ProviderCapabilities` exact.
3. `method_implemented` : override réel de la méthode `ProviderSession`
   (hériter du stub « unsupported » ne suffit pas).
4. `session_ready` : plugin prêt, session connectée, provider/credential
   concordants, emprunteur exact présent dans les comptes de la session.

L'état `supported` désigne uniquement une **disponibilité technique
déclarée** ; il ne prouve **ni éligibilité du prêt, ni consentement, ni
résultat distant**. Une action mutante reste subordonnée à #3121 et aux
contrôles métier existants. Ce module ne fait aucun GET/POST, ne déclenche
aucun accès fournisseur et ne consomme aucune tâche.

| Opération | Capability manifeste v1/additive | Booléen | Méthode |
| --- | --- | --- | --- |
| `loan.list` | `sync.loans` | `loans` | `loans` |
| `loan.borrow` | `acquisition.borrow` | `borrow` | `borrow` |
| `loan.download_token` | `acquisition.download` | `digital_download` | `download_digital` |
| `loan.renew` | `lending.renew` (future, non publiée) | `renew_loan` | `renew` |
| `loan.return` | `lending.return` (future, non publiée) | `return_loan` | `return_digital` |

**Paris** : lecture/emprunt/téléchargement déclarés v1 ; retour et
renouvellement historiques disponibles dans le code, mais les capacités
spécifiques correspondantes ne sont **pas** publiées : `unknown` dans
ce nouveau contrôle, **sans changer les anciennes commandes**.

**BnF Prêt** : `loan.list` déclaré ; `download_digital` existe à des
fins de staging ACSM explicite mais **aucune** capability publique
`acquisition.download` / `digital_download` n'est présente : elle
ne peut être promue par introspection en action générique.

Les raisons sont des codes fermés
(`manifest_unverified`, `manifest_descriptor_conflict`,
`method_unimplemented`, `session_unavailable`,
`legacy_capability_unmapped`, etc.). Aucun nom de compte, URL ou
exception fournisseur n'entre dans ce résultat.

**À faire avant activation générale :** finaliser le modèle par action
compte/prêt, valider capabilities manifests au packaging et à l'activation,
introduire consentements/gardes #3121 et tests de conformité #3122.
La présence d'une capacité `supported` ne doit jamais directement
activer un bouton sans ces autres validations.
