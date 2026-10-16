# #1346 — politique de rafraîchissement de source, tranche Core

## Contrat minimal

`core/source_refresh_policy.py` porte une politique **indépendante par source**,
persistée sous `sync.source.refresh.policy.sync:<hash>` avec schéma strict
`cifereca.source-refresh-policy/1`.

Les modes autorisés concernent **uniquement l'inventaire et les droits** :

- `manual` (défaut implicite, y compris après lecture de l'ancienne
  configuration `kobo_backup.enabled`) : aucun lancement programmé ;
- `on_start` : admission uniquement au déclencheur `startup`, avec suppression
  d'une seconde demande du même démarrage et intervalle minimal ;
- `periodic` : admission uniquement au déclencheur `periodic`, après un
  dernier essai plus ancien que l'intervalle explicitement configuré.

Intervalle entier strict de 15 à 10 080 minutes. Une source désactivée,
déconnectée, incapable de synchroniser ses notices, une phase `running`, un
timestamp invalide/futur ou une politique malformée ne sont **jamais dus**.
Aucun champ fournisseur, compte, token, titre, chemin ou URL n'entre dans
la politique ; les clés de stockage valident la forme `sync:<hash>`.

## Frontières et migration

Cette tranche ne change pas `opening_sync`, ne crée **aucune tâche**, n'installe
**aucun timer**, ne télécharge aucun fichier et ne publie pas de commande
d'activation automatique de téléchargement. Les anciens booléens globaux
restent lus par leur **ancien** parcours de compatibilité ; ils ne sont
**pas** transformés implicitement en `periodic` / `on_start` par compte.
La méthode `save_source_refresh_schedule` est réservée au Core après
contrôle d'appartenance de la source. Ce lot fournit la lecture, la validation,
la persistance explicite et le prédicat d'admission indépendants et testables.

**Intégration restante dans #1346 :** API de configuration par source
autorisée par le broker, ordonnanceur effectif + anti-concurrence,
adaptation des réglages CoreUI, migration/dépréciation des anciens réglages
globaux **sans autoriser de nouvelles acquisitions implicites**.
Les tests ici n'établissent ni exécution d'un timer, ni recette utilisateur.
