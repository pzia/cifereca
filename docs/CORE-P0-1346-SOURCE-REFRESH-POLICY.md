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

Le module de politique ne crée aucune tâche et ne télécharge aucun fichier.
Le broker et les ordonnanceurs GTK décrits ci-dessous appliquent cette
politique aux inventaires Kobo et Numilog. Les anciens booléens globaux
restent lus par leur **ancien** parcours de compatibilité ; ils ne sont
**pas** transformés implicitement en `periodic` / `on_start` par compte.
La méthode `save_source_refresh_schedule` est réservée au Core après
contrôle d'appartenance de la source. Ce lot fournit la lecture, la validation,
la persistance explicite et le prédicat d'admission indépendants et testables.

Les réglages CoreUI et les déclencheurs réels sont raccordés au broker.
La dépréciation des anciens réglages globaux reste distincte : leur présence
ne vaut jamais opt-in aux nouvelles politiques par compte.


## Tranche broker CORE (#1963)

Le protocole public optionnel `SourceInventoryRefreshScheduleBroker` rend
désormais configurables les modes `manual`, `on_start` et `periodic` pour
la **découverte/droits Kobo ou Numilog**, indépendamment de
`SourceMaterializationScheduleBroker` (acquisition). Les méthodes sont :

- `inventory_refresh_schedule(source_id)` : lecture passive, mode manuel implicite.
- `set_inventory_refresh_schedule(source_id, mode, interval_minutes=60)` :
  opt-in explicite et rattachement strict `identité/provider/credential/compte`.
- `synchronize_scheduled_inventory(identity_id, trigger)` : dispatch borné
  uniquement pour un déclencheur externe `startup` ou `periodic`, en passant
  par `synchronize_source` avec `SyncRequest` strictement ciblé.

Une source désactivée, déconnectée, dupliquée ou rebindée n'est pas éligible ;
le mode `manual` reste réversible même si la source a été déconnectée.
La dernière tentative est utilisée pour le backoff, y compris après échec.
Un contrôle singleflight bloque les demandes simultanées sur **un broker**
et le stockage persistant limite les demandes rapprochées après recréation
du broker. **Aucun verrou distribué inter-processus n'est revendiqué.**

Le broker reste passif : seuls les ordonnanceurs décrits ci-dessous
installent le timer ou transmettent le signal de démarrage. Une politique
persistée ne prouve pas qu'un processus GTK est ouvert ni qu'un run a réussi.
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
jamais un consentement de sauvegarde des fichiers.** Le contrat générique
`local_materialization_schedule` ne programme pas Numilog. La matérialisation
Numilog utilise désormais les méthodes distinctes décrites ci-dessous ; elle
n'est pas autorisée par un réglage d'inventaire.
Les tests utilisent des comptes et identités fictifs, refusent l'exécution
après révocation ou paramètres corrompus, et vérifient les deux horloges
`on_start`/`periodic` sans mélanger la source ni déclencher de DRM.

La configuration GTK et les déclencheurs d'inventaire au démarrage et
périodiques sont raccordés pour Numilog comme pour Kobo. La sauvegarde
Numilog opt-in au démarrage est également livrée, indépendamment de cet
inventaire (voir section suivante). La livraison Numilog/ACSM/ADEPT et deux
livres visibles en Bibliothèque ont été confirmés par le propriétaire le
18/10/2026 (#2024) ; cela ne prouve pas un essai réel de chaque planification.
Aucun succès d'inventaire ne vaut sauvegarde de fichier réussie.

## Sauvegarde Numilog distincte, au démarrage (#1905)

Dans Synchronisation, « Sauvegarde automatique des achats Numilog » est en
**mode manuel par défaut**, pour chaque source/identité/compte. L'action
« Activer au démarrage… » exige un aperçu local puis une seconde confirmation
explicite ; ouvrir la page, lire l'aperçu ou activer l'inventaire ne donne aucun
consentement de téléchargement. La désactivation de la source révoque ce
consentement atomiquement ; la révocation reste possible après déconnexion.
Les anciens réglages ou une confirmation périmée ne sont pas réinterprétés.

Le broker expose `numilog_materialization_schedule`,
`prepare_numilog_materialization_consent`,
`confirm_numilog_materialization_consent`,
`revoke_numilog_materialization_consent` et
`numilog_materialization_run_status`. Seule l'exécution
`synchronize_scheduled_numilog_materialization(identity_id, "startup")`
après ouverture peut acquérir les candidats dûment consentis et réadmis.
Les callbacks d'inventaire et de rendu n'appellent jamais cette exécution.

La tâche est unique par fenêtre, annulable entre deux livres et bornée à
**8 tentatives par source et par ouverture**, avec une admission persistée
espacée d'au moins **60 minutes**. Chaque source garde sa propre limite : un
échec ne prive pas un autre compte consenti de son essai. Le manifeste privé
est borné à 2 048 entrées ; un dépassement bloque plutôt que tronquer les droits.
Connexion, session, racine, propriétaire, consentement et achat exact sont
revérifiés sous verrou. Les fichiers déjà attestés et les reçus en attente
ne provoquent pas de nouveau téléchargement ; une indexation interrompue
se reprend explicitement hors réseau, comme décrit dans
[SOURCE-MATERIALIZATION-CONFIRMATION.md](SOURCE-MATERIALIZATION-CONFIRMATION.md).

Le bilan affiché distingue tentative, succès, partiel, échec et blocage, avec
des compteurs bornés et le prochain démarrage admissible. **Aucun timer de
sauvegarde périodique Numilog ni service hors application n'est annoncé** :
le mode périodique concerne uniquement l'actualisation des notices/droits.
L'activation de cette fonction sur un compte réel reste une action utilisateur,
pas un effet d'installation, de migration, de test ou de consultation.


## Tranche CoreUI — ordonnanceur périodique opt-in (#1346)

Le module `gui/source_inventory_timer.py` installe un heartbeat GTK de 60 s
**uniquement si** une identité configurée possède une source Kobo ou Numilog connectée,
activée, `sync_capable`, avec politique `periodic` explicitement persistée.

- Le déclencheur fait uniquement des lectures passives du broker pour
  décider si un compte est dû (`source_refresh_due` et `last_attempt`).
- Une tâche `source-sync` en arrière-plan transmet explicitement les
  identités dues à `synchronize_scheduled_inventory(identity_id, "periodic")`.
  Le broker réévalue la liaison durable et la source avant tout I/O distant.
- La souscription au broker reconfigure ou retire le timer lorsque la
  préférence, l'état du compte ou la connexion changent. Les notifications
  asynchrones sont transmises via `GLib.idle_add`, sans accès GTK depuis le
  worker. La fermeture annule le timer et se désabonne.
- Aucun `materialize_source`, `kobo_backup`, URL ACSM ou flux ADEPT n'est
  invoqué ou autorisé. Aucune préférence de téléchargement n'est lue.
- Le worker poursuit les autres identités après une exception ou un reçu
  échoué/invalide, puis termine en erreur publique fixe si un bilan manque
  ou échoue. Une tâche terminée ne transforme pas un résultat partiel en succès.

**Limites :** le heartbeat existe seulement pendant la vie de la fenêtre ;
il ne constitue pas un service système hors application. L'ordonnanceur
ne remplace pas le cycle historique `opening_sync` et n'assure pas de verrou
distribué au-delà du verrou Core utilisé à l'exécution. Le démarrage suit
le chemin distinct décrit ci-dessous.


## Horloge de découverte/droits distincte de l'ouverture (#1346, #1905)

Un statut `sync.source.state.scope=opening` représente la synchronisation
générale (notamment les prêts) et **n'atteste pas** que `session.library()`
a réellement lu les livres et droits du compte Kobo. Il ne peut donc ni
prolonger un succès `discover_rights` ni repousser un `periodic` opt-in.

Les phases Kobo et Numilog conservent dans le même état JSON source trois champs
bornés : `inventory_last_attempt`, `inventory_last_success` et
`inventory_status`. Ils sont mis à jour **seulement** par un
`synchronize_source` de ces fournisseurs disposant de `personal_library`. En cas d'échec
ou de synchronisation partielle, la tentative throttle la reprise mais ne
devient jamais un succès. La projection `SyncPhaseState(discover_rights)` et
l'admission du timer périodique lisent exclusivement cette preuve de phase ;
l'état générique `running` continue d'interdire une concurrence avec une
synchronisation d'ouverture active.

Une base historique sans horloge de découverte est projetée `idle`,
jamais comme un succès inventé ; un horodatage inventé ou corrompu la bloque
en `unknown`. L'horloge `materialize` et les autorisations d'acquisition
ne sont pas modifiées. Aucune migration implicite d'un opt-in global.


## Déclenchement `on_start` Kobo et Numilog après l'ouverture (#1346, #1905)

Le mode `on_start` n'est **pas** une seconde synchronisation globale
pendant le chargement initial. `startup_coordinator` transmet une marque
éphémère à **son unique** appel `run_opening_sync()`. Le callback GTK de
succès, exécuté après la restitution du verrou Core d'ouverture, peut alors
appeler `source_inventory_startup.dispatch_after_opening()`.

L'ordonnanceur lit uniquement les sources Kobo ou Numilog avec politique
`on_start` explicite, la phase `discover_rights` attestée, une identité
configurée non ambiguë, une connexion active et l'activation de la source.
Les résultats absents, malformés, inactifs, en cours ou non dus n'autorisent
aucune tâche. Les permissions et le rattachement du compte sont revérifiés
avant le lancement de chaque source par `synchronize_scheduled_inventory`,
ainsi qu'après une éventuelle attente dans la file GTK.

Ce parcours est **à usage unique par fenêtre** ; ni un clic de synchronisation
manuelle, ni une vérification profonde, ni une reconnexion tardive n'héritent
d'une autorisation de démarrage. Le worker utilise `origin=automatic`, une
priorité fournisseur et une clé de déduplication spécifique, distincte du
timer périodique. Il ne télécharge pas d'ebook. La coordination initiale
d'inventaire utilise les horloges de phase, sans reprendre le timestamp
global d'ouverture comme preuve de découverte des achats.

## Capacité déclarative après raccordement des déclencheurs

Les sources Kobo et Numilog dont la session atteste `personal_library`
exposent `scheduled_refresh` dans `SyncSourceRef.sync_capabilities` : le Core
dispose de vrais déclencheurs d'inventaire `on_start` et `periodic`.
Cette capacité décrit une disponibilité, jamais une autorisation. La politique
reste `manual` par défaut pour chaque source, sans héritage des réglages
d'acquisition ; la lecture du contrat ne crée aucun timer ni appel réseau.
Le broker revalide le compte, la session, l'identité, la connexion et la
politique juste avant chaque actualisation. Les autres fournisseurs et les
sessions sans inventaire personnel n'annoncent pas cette capacité.
