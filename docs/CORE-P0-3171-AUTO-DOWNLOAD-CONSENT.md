# #3171 / #2797 — Consommation du consentement global dans la Sync canonique

## Arbitrage produit du 30 octobre 2026

L’ensemble physique à synchroniser est **l’ensemble des références pour lesquelles
le fournisseur authentifié présente une offre de téléchargement actuelle**.
Un droit bibliographique ou une métadonnée locale `owned` manquant, obsolète ou
inconnu n’est pas une condition supplémentaire au téléchargement.

Un achat/prêt est acquis si — et seulement si — la référence fournisseur exacte,
le compte, l’offre actuelle, les restrictions du fournisseur, le consentement
persisté pour cette source/type et les contrôles d’intégrité sont validés.
L’identité du fichier est le SHA-256 final et non son chemin physique.

## Un seul exécuteur, des sources de consentement compatibles

La **Sync manuelle, la Sync globale et la Sync d’ouverture** utilisent
`qualify_selected_offers → execute_admitted_convergence →
publish_completed_acquisition`. Les fichiers déjà vérifiés par
`OriginLink → Artifact/SHA → Placement` ne sont jamais téléchargés à nouveau.
Une présence de fichiers sans provenance ne constitue ni un droit ni un veto :
l’offre et le consentement positifs conduisent au repli d’acquisition.

Le lecteur Core `source_acquisition_consent` prend, dans sa même transaction
SQLite, les commutateurs explicites disponibles pour la source exacte :

| Fournisseur / type | Préférence persistée de téléchargement automatique |
| --- | --- |
| Numilog / achats | `sync.source.numilog.materialization.consent.v1.sync:<id>` = `on_start:v1` **avec epoch valide** |
| BnF Prêt / emprunts | `sync.source.bnf_auto.sync:<id>` = `1` |
| Kobo / achats | `sync.source.materialization.on_start.sync:<id>` = `1` |

`<id>` est l’empreinte déterministe et exacte du profil, du fournisseur, du
credential et du compte. Aucun autre profil, type de droit ou fournisseur
ne reçoit cette autorisation. **`kobo_backup.enabled` historique, catalogue,
présence physique, ancien cache et sélection graphique n’octroient jamais de
permission.**

Le mode ON autorise **toutes les SourceReferences positives et synchronisables
déjà observées de cette source/type**. OFF révoque également les anciennes
autorisations individuelles, à chaque admission et dans la transaction finale.
Absence de commutateur conserve uniquement le mécanisme explicite par droit
préexistant pour les installations n’ayant pas encore adopté l’option globale.
Lorsque le commutateur a été configuré, la page Sync ne montre plus les
boutons redondants « Autoriser ce droit… ».

Pour Numilog, l’epoch persistée est figée autour de la lecture de consentement
et comparée avant/après GET et à la publication atomique : OFF puis ON ne peut
réactiver l’ancienne opération. Pour les autres sources, une révocation est
recontrôlée à tous les points d’admission et à la publication ; la sémantique
d’epoch devra être ajoutée à leurs préférences si leur interface doit supporter
une garantie identique pour le cycle OFF→ON dans une même opération.

## Droit descriptif absent et livraison

Une observation `ProviderRightObservation` d’état `unknown` peut annoncer
`delivery=available` **uniquement si elle provient d’une réponse authentifiée
fournisseur** et possède un `delivery_offer_id` exact. L’état `inactive`,
l’absence d’offre, une preuve `legacy_unverified` et les scopes ambigus
restent des refus. Une référence synchronisable peut être persistée, sans créer
un `Entitlement` légal synthétique.

Le contrat Plugins n’autorise ni la publication directe de fichiers ni
l’adoption fondée sur le titre : le Core conserve le contrôle de l’offre
sélectionnée, du verrou global, de l’ADEPT/DRM, du SHA et de la transaction
Artifact/Placement/OriginLink. Un refus n’arrête pas les autres achats.

## Séparation de l’ancien worker Numilog

Quand le fournisseur Numilog connecté expose le contrat canonique complet
(observation + sélection d’offre + livraison ciblée), le worker legacy
`numilog_materialization_startup` n’est pas inscrit : la même autorisation ne
peut pas créer **deux files de téléchargement**. L’ancien worker reste disponible
pour un adaptateur historique dépourvu de ce contrat.

## Recette et réserves

- `tests/test_core_p0_3171_source_auto_consent_bridge.py` :
  portée profil/type, ON/OFF, ancien grant, publication, état du droit inconnu,
  identité et epoch.
- `src/plugins/provider-numilog/tests/test_six_purchase_core_conformance.py` :
  vrai `NumilogSession`, HTTPX simulé, EPUB/ACSM, six références, trois
  placements sans origine, Sync individuelle/globale, consentement source,
  déduplication SHA, zéro GET à la seconde Sync, absence du second worker,
  révocation et OFF→ON pendant livraison.
- `tests/test_plugin_api_p1_3252_provider_rights_observation.py` :
  offre authentifiée avec droit inconnu, refus d’inactivité/legacy.

Les tests utilisent des données et transports synthétiques. Ils ne prouvent
**pas** la réparation des six livres et des reçus historiques sur le poste
de production. Exiger journal terrain post-06:16:34, contrôle de l’absence de
double GET, des six OriginLinks/Placements/SHA et de la seconde Sync.
Fusion conditionnée à CI Core, Plugins, E2E et recette installation/rollback.
