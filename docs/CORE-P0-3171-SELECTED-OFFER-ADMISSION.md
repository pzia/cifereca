# Core P0 #3171 — admission des préflights exacts Plugins #3252

Cette tranche raccorde l'entrée optionnelle BnF/Numilog fusionnée par
[#3605](https://github.com/pzia/cifereca/pull/3605) à **la seule Sync Core
existante**. Aucun traitement parallèle, aucune table, aucun nouveau provider.

## Ordre de décision

1. La Sync maintient le **même verrou acquisition/maintenance** et reçoit la
   seule observation typée initiale A, déjà validée/persistée en références.
2. **Uniquement si** un routeur interne de livraison finale est enregistré
   et si le lecteur de consentement exact est installé, la Sync recharge les
   `SourceKey` durablement consenties dans SQLite.
3. Pour une source positive active, non déjà servie par son
   `OriginLink(provider) → SHA → Placement` actuellement haché dans le profil,
   et dont l'offre reste à qualifier, le Core vérifie consentement courant,
   propriétaire du credential, compte, session, epoch et source active.
4. Le Core appelle l'unique interface optionnelle fournisseur
   `rights_selected_delivery_offer_snapshot(account, edition_id=...,
   right_id=..., kind=..., branch=..., explicit_user_action=True)`.
   Le booléen n'est **pas** la permission : celle-ci existe déjà en base.
5. Le résultat doit être une enveloppe `ProviderRightsSnapshot` positive,
   **partielle**, du même scope, même branche, exactement un seul droit actif
   attesté et **offre opaque disponible**. Rebind, révocation, mauvaise
   édition, offre inconnue/contradictoire ou absence de méthode : aucune
   promotion de l'offre.
6. L'exécuteur Core préexistant contrôle **encore** consentement, session,
   offer exact, octets finaux/SHA, reçu de livraison distinct, ADEPT, journal
   crash-safe puis transaction `Artifact + Placement + OriginLink`.

## Effets et limites

- Pas de préflight lorsqu'aucun consentement exact, aucun routeur de livraison
  final, ni lorsque le fichier est déjà attesté présent.
- Un snapshot partiel ne démontre jamais une disparition du droit.
- Aucune URL signée, preuve de B ou faux `CompletedDelivery` ne sort de
  l'offre. Aucune écriture du consentement ou d'un lien fournisseur par plugin.
- La route productive `_canonical_right_delivery` et le reçu EPUB/PDF final
  post-ACSM/ADEPT restent **à livrer** : cette tranche **ne déclenche donc
  aucun téléchargement réel** en production. Elle n'autorise aucune clôture
  #3171 ni #3252.
- Tests offline : permission absente, retrait pendant préflight, rebind epoch,
  réponse étrangère, sélection 1/N, idempotence seconde Sync avec zéro
  nouveau préflight et zéro GET de fichier. Réception fournisseur réelle,
  crash et migration legacy restent distincts.
