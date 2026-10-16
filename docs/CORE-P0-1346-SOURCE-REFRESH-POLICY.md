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


## Tranche broker CORE (#1963)

Le protocole public optionnel `SourceInventoryRefreshScheduleBroker` rend
désormais configurables les modes `manual`, `on_start` et `periodic` pour
la **découverte/droits Kobo** uniquement, indépendamment de
`SourceMaterializationScheduleBroker` (acquisition). Les méthodes sont :

- `inventory_refresh_schedule(source_id)` : lecture passive, mode manuel implicite.
- `set_inventory_refresh_schedule(source_id, mode, interval_minutes=60)` :
  opt-in explicite et rattachement strict `identité/Kobo/credential`.
- `synchronize_scheduled_inventory(identity_id, trigger)` : dispatch borné
  uniquement pour un déclencheur externe `startup` ou `periodic`, en passant
  par `synchronize_source` avec `SyncRequest` strictement ciblé.

Une source désactivée, déconnectée, dupliquée ou rebindée n'est pas éligible ;
le mode `manual` reste réversible même si la source a été déconnectée.
La dernière tentative est utilisée pour le backoff, y compris après échec.
Un contrôle singleflight bloque les demandes simultanées sur **un broker**
et le stockage persistant limite les demandes rapprochées après recréation
du broker. **Aucun verrou distribué inter-processus n'est revendiqué.**

**Non livré par cette tranche** : installation d'un timer GTK, appel automatique
au démarrage de l'application, coordination avec les cycles historiques d'ouverture,
et acquisition programmée. Un mode persisté `periodic` ne signifie pas qu'un
timer est effectivement actif tant que l'ordonnanceur n'est pas raccordé.
L'implémentation `synchronize_source` conserve provisoirement sa valeur
historique `scope=manual`, même lors d'un dispatch explicite planifié ;
le résumé de dispatch porte `trigger` et `phase=discover_rights`.



## Extension Numilog : actualisation des achats/droits (#1905)

Le même broker **`SourceInventoryRefreshScheduleBroker`** accepte désormais,
en plus de Kobo, une source **Numilog personnelle** dont l'identité,
le credential, le compte enregistré et **l'unique session privée courante**
sont tous vérifiés par `numilog_inventory_binding_valid`. Un état de source
malformé, une session remplacée ou dupliquée, une révocation, une source
désactivée ou un compte réattribué interdisent l'exécution distante.

`manual` reste le défaut. Les appels `set_inventory_refresh_schedule`
n'effectuent aucun accès réseau. Sur appel explicite d'un déclencheur
`synchronize_scheduled_inventory(identity_id, "startup"|"periodic")`, seul
le compte et l'identité consentis et dus sont rafraîchis ; le broker réutilise
`synchronize_source` qui lit l'inventaire authentifié et les droits sans
demander de lien de livraison ni télécharger d'EPUB/ACSM.

**Frontière impérative : le consentement d'actualisation des achats n'est
jamais un consentement de sauvegarde des fichiers.** Pour Numilog,
`local_materialization_schedule` continue à répondre `not_supported`
et aucun broker d'acquisition automatique n'est ajouté dans ce lot.
Les tests utilisent deux comptes et deux identités fictifs, refusent la
revocation et les paramètres corrompus, et vérifient les deux horloges
`on_start`/`periodic` sans mélanger la source ni déclencher de DRM.

**Non livré :** raccordement effectif des déclencheurs à une tâche au
démarrage et au timer, configuration GTK, acquisition ciblée opt-in,
réconciliation ADEPT/DRM et succès de téléchargement sur compte réel.
Il ne faut pas présenter une politique `on_start` ou `periodic` stockée
comme une synchronisation automatiquement active dans l'application.
