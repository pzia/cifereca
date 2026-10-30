# Consentement générique de synchronisation — Core #3719

## Contrat livré

Le protocole public optionnel `SourceSyncConsentBroker`, exporté par
`cifereca.plugin_api`, ajoute au broker Synchronisation :

```python
state = broker.source_sync_consent(source_id)
# supported, configured, enabled, revision, valid
state = broker.set_source_sync_consent(
    source_id, enabled=True, expected_revision=state['revision'],
)
# Révocation explicite, même si la source est déconnectée ou désactivée :
state = broker.set_source_sync_consent(
    source_id, enabled=False, expected_revision=state['revision'],
)
```

Ces opérations sont locales et ne lancent ni inventaire, téléchargement,
ADEPT, scan ou publication de fichier. L'activation nécessite le contrat
fournisseur v2 complet et un compte-source Core courant et activé. Elle peut
précéder le premier inventaire : toutes les ressources positivement livrables
actuelles et futures de cette source sont couvertes, achats et prêts inclus.
Une capacité inconnue/indisponible, une archive, un compte différent ou une
licence ADEPT bloquée n'obtient aucune autorisation de livraison.

La clé de politique contient exactement le profil, le fournisseur, la
connexion et le compte. Aucun identifiant de droit, ressource, édition,
branchement d'inventaire, titre, chemin ou SHA n'entre dans le consentement.
Les sources de catalogue gardent leur configuration indépendante ; ce contrat
porte sur l'acquisition des fichiers pendant la Sync.

## Persistance, révocation et courses

Une seule ligne JSON `cifereca.source-sync-consent/1` dans `metadata` contient
le scope exact, `enabled` et une génération aléatoire. Sa clé commence par
`sync.source.acquisition-policy.v1.`. La validation de scope, le CAS et
l'écriture sont dans une même transaction `BEGIN IMMEDIATE` ; un échec SQL
annule simultanément l'état et la génération.

Le premier appel passe `expected_revision=None`. Ensuite, le client doit
relire et passer la révision courante. Un ancien client ne peut pas écraser
la révocation d'un autre client. Un réglage corrompu est explicitement
`valid=False, enabled=False` ; sa révision opaque permet une réparation par
CAS après une action explicite, sans l'assimiler à une préférence absente.

Le moteur fige la génération de **toute** source, sans sélection par nom de
fournisseur, avant de lire son autorisation. Il la revalide autour de l'offre,
de la livraison et dans la transaction finale. ON→OFF→ON ne ressuscite pas
l'ancien GET. La publication vérifie également l'activation de la source
dans sa propre transaction SQLite.

Pour une source déjà configurée via ce contrat, `set_enabled(False)` révoque
l'acquisition et avance la génération dans la même transaction que le
commutateur de source. Réactiver l'inventaire ne rétablit pas implicitement le
consentement d'acquisition ; un nouvel opt-in explicite est nécessaire.

## Compatibilité et migration restante

Aucune permission par droit ni préférence historique n'est transformée en
consentement global. Sans réglage générique, les anciens réglages explicitement
configurés continuent d'être lus par le pont de compatibilité existant.
Dès qu'un réglage générique existe, il est prioritaire sur les anciennes
permissions ; OFF, malformation ou lecture impossible ne permettent aucun
repli vers un ancien worker.

Les Sync source et ouverture utilisent alors le moteur canonique, y compris
pour BnF. Les anciens appels automatiques de prêts et le worker Kobo sont
écartés pour ce compte-source, même si l'inventaire canonique échoue. Les
lectures d'inventaire et les données historiques ne sont pas supprimées.

**Consommation CoreUI :** la page Sync courante utilise ce protocole pour un
contrôle d’acquisition par source et n’affiche plus les grants par droit.
Un réglage non migré exige un choix explicite ; le rendu n’écrit rien.
Voir [le contrat CoreUI](CORE-P0-3171-CANONICAL-CONSENT-COREUI.md).
Les hôtes anciens sans ce protocole gardent leurs autres contrôles de
compatibilité. Les tables provider du pont et les parcours legacy des sources
non migrées restent à retirer. Cette tranche ne clôt ni #3719 ni #3171 ; elle
ne livre pas la déduplication physique post-placement ni une recette fournisseur
réelle.

## Preuves

`tests/test_core_p0_3719_source_sync_consent.py` : source inconnue du Core,
opt-in avant inventaire, ressources futures et deux catégories sans grant
individuel, OFF prioritaire, CAS/concurrence de deux connexions SQLite,
rollback, portée complète, corruption, redémarrage, ON→OFF→ON pendant offre,
livraison et publication, et désactivation/réactivation de l'inventaire.

`tests/test_core_p0_3719_consent_legacy_dispatch.py` : vrai adaptateur BnF avec
transport synthétique, Sync explicite/ouverture et acquisition simulée au
point de livraison ; un seul exécuteur malgré l'ancien réglage ON, zéro
ancienne acquisition sous OFF. Kobo : OFF interdit le repli legacy même sans
observation typée. Ces tests ne prouvent aucun accès fournisseur réel.
