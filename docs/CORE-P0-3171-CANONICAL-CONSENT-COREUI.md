# #3171 — Consentements canoniques par droit, dans Synchronisation

## Raison

Les premiers lots #3171 ont livré le stockage `SourceKey` et l'exécution
canonique par la Sync explicite puis l'ouverture (#3653), mais aucun utilisateur
ne pouvait activer ou révoquer **un droit exact** via CoreUI. En particulier,
l'ancien `kobo_backup.enabled`, le planning Numilog `on_start`, une notice,
un ISBN et une simple présence de fichier ne constituent pas un nouveau
consentement canonique. La recette du **29 octobre 2026** contient trois
Numilog `needs_reconciliation` : ils ne deviennent pas téléchargeables par
le seul ajout de ces contrôles, car leur état de provenance reste distinct.

## Contrat fermé (broker Core optionnel)

`SynchronizationBrokerImpl.canonical_acquisition_choices(source_id)` lit
**uniquement** SQLite : pages de 48 SourceReferences déjà persistées,
du compte exact, accessibles via un index stable ; notamment pour les
522 références du compte Kobo observées le 29 octobre. Retour : `reference` (empreinte opaque stable
de 64 caractères, **pas** un jeton d'autorisation), `kind` achat/prêt,
`allowed` et `eligible` booléens. Aucun identifiant fournisseur/credential,
numéro de prêt, titre privé, URL signée ni chemin n'est exposé dans l'API/UI.

`set_canonical_acquisition_choice(source_id, reference, allowed=bool)`
retrouve ce sélecteur à partir du compte toujours lié, puis appelle **la
primitive Core déjà livrée** `set_source_acquisition_consent` ; le `BEGIN
IMMEDIATE` revalide l'attestation positive, la source active, l'identité,
le credential, l'édition et le prêt avant d'écrire un grant. L'échec est
fermé. Révoquer un grant ancien reste possible après disparition du droit,
sans suppression du droit ni d'un fichier. Les records malformés sont refusés. Les droits au-delà de 48 sont exposés
par les pages suivantes sans chargement fournisseur ni grant automatique.

La page Synchronisation ajoute, dans les outils du compte visible, une
action par droit affiché, avec navigation entre pages de 48 droits.
**Autoriser** et **Révoquer** exigent un clic puis
une confirmation explicite ; le bouton respecte le scope CoreUI capturé
(page/profil/source), puis la primitive Core revérifie sous SQLite. Le simple
affichage ne déclenche jamais de provider GET, d'ACSM ou de publication.

## Garanties et limites

- Les grants durables sont exacts et un par SourceKey, **jamais** tous les
  futurs droits d'un compte ni une préférence globale.
- La Sync, y compris l'ouverture, redemande l'offre choisie au provider et
  vérifie droit/binding/consentement avant et après GET, avant SHA et commit
  atomique OriginLink/Artifact/Placement.
- Les labels génériques « achat/prêt n°N » préservent les IDs privés mais ne
  remplacent pas une notice lisible. Un affichage bibliographique riche reste
  à faire via la Notice canonique attestée sans interpréter le titre comme
  autorité de droits.
- Les permissions réelles fournisseur, la reprise ADEPT one-use, les achats
  Numilog legacy `needs_reconciliation`, la migration `backup_items`
  #3196 et la recette physique #3642 ne sont **pas** réglés ici.
- Les producteurs/transport HTTP, le contrat optionnel #3252 et les modules
  Diagnostics C ne sont pas modifiés.

## Régressions

`tests/test_core_p0_3171_canonical_consent_broker.py` : SQLite v14, un
grant/révocation persistant, changement de scope, autre compte, mauvais
sélecteur, droit devenu inactif, duplicat d'Account, réaffectation de
credential ; **zéro GET**. Les suites Core et GTK existantes doivent rester
vertes sur le dernier HEAD de la PR.

**Critère restant pour #3171** : réception manuelle GTK et fournisseurs
réels, y compris vérification de l'offre exacte et seconde Sync sans GET.
