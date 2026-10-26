# P1 #3120 — introspection de capacités sans effets fournisseur

L'API `assess_lending_operation` a vocation à être appelée lors de la
présentation des capacités d'un fournisseur. Une lecture de
`session.accounts` peut paraître sans danger, mais il s'agit d'une
**propriété Python surchargeable**, susceptible d'exécuter un GET, une
authentification ou n'importe quel code plugin.

## Correctif

- Lire le manifeste et le descripteur exact, sans instancier un provider.
- Vérifier les overrides de méthodes avec `inspect.getattr_static`, sans
  déclencher de descripteur/metaclass.
- Pour une session `ProviderSession`, lire uniquement les champs mémoire
  d'instance déjà initialisés (`connected`, `provider_id`,
  `credential_id`, `_accounts`) ; ne jamais exécuter une propriété plugin.
- Cette lecture directe n'est permise que si la propriété `accounts` reste
  celle de la classe de base. Une implémentation personnalisée, potentiellement
  dynamique, demeure **indisponible/non vérifiée** dans l'évaluateur,
  sans affecter les appels métier historiques du provider.
- Limiter le nombre de comptes inspectés à 512 et valider les types
  primitifs du compte demandé, sans connexion, session refresh, prêt ni
  téléchargement.
- Exiger **une correspondance unique** du quadruplet
  `identity/provider/credential/borrower` dans les comptes mémoire : deux
  enregistrements identiques (ou le même objet deux fois) rendent l'opération
  `unavailable`. La simple existence de la méthode ne dénoue pas
  l'ambiguïté de portée et n'autorise aucun bouton.

Cette vérification ne confère aucun droit, aucun consentement et ne modifie
aucune opération provider. Elle n'est pas un contrôle de permission métier ;
la frontière #3121 reste distincte.

## Tests synthétiques

`tests/test_core_p1_3120_lending_capabilities.py` simule un getter
`accounts` réseau, un getter `connected` réseau, un métaclasse hostile,
une méthode `borrow` à effet dans une propriété et 513 comptes liés.
Les introspections doivent refuser ou fonctionner à partir des champs
inertes, **sans un seul appel** vers ces getters, méthode ou réseau.

Restant #3120 : adoption de la disponibilité réelle dans chaque commande
Core/UI après stabilisation #3121, conservation des capacités legacy,
qualification des actions Paris et recettes GTK/packaging #3122.
