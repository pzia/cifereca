# P0 #4858 — D1 : suppression du repli d’aperçu Kobo 0.7 sous CAS canonique

## Écart constaté

Les exécuteurs W2 et la matérialisation D2 refusaient déjà de réutiliser
leur permission historique après configuration d’une politique CAS de
synchronisation canonique (ON, OFF, illisible). Le lecteur D1
`kobo_backup_preview`, en revanche, ne basculait vers l’audit canonique
que pour une session présentant l’ABI A→B **complète**. Une session
déconnectée ou rétrogradée vers une ABI absente/partielle revenait alors
aux données `backup_items`, dont les anciens statuts et checkpoints
pouvaient alimenter les compteurs de présence et de candidats.

## Contrat de lecture après correction

Pour le credential/identity Kobo demandé, D1 sonde les emprunteurs
exacts issus du registre Core et des liaisons durables
`provider_accounts` (requête SQLite bornée). Une politique CAS
`on`, `off`, `invalid` ou devenue illisible empêche tout
repli vers les états 0.7 si l’ABI de livraison est incomplète.

- **Aperçu avec rafraîchissement** : refus explicite
  `canonical_kobo_preview_abi_unavailable` avant toute lecture de
  manifest, ancien préflight privé ou téléchargement ; une ABI partielle
  n’est pas une attestation fournisseur A.
- **Aperçu sans rafraîchissement** : audit **uniquement** des preuves B
  canoniques historiquement publiées, sans compteur positif de droit
  actuel, d’autorisation GET ou d’édition à récupérer. L’inspection
  d’une session absente utilise les coordonnées SQL, sans connecter Kobo.
- **CAS absent** : la lecture legacy demeure temporairement compatible
  tant que le moteur W2 non canonique existe.
- **ABI complète** : le chemin A→B existant reste propriétaire des
  décisions. Son contrôle de portée revalide désormais, après l’appel
  fournisseur, l’identité, le provider, le credential, l’emprunteur
  et la **clé exacte du registre** ; seul le label adapter Kobo-Vault
  `identity_id="default"` peut différer de l’identité Core SQL.

Aucune archive n’est supprimée, aucun droit A/B créé, aucune mutation
`backup_items`, aucun GET, aucune migration ni changement des autres
fournisseurs. Les liaisons manquantes/incohérentes refusent la projection
canonique plutôt que d’inférer un compte depuis les archives.

## Régressions

`tests/test_core_p0_4858_d1_partial_cas_no_legacy_preview.py` :
CAS ON/OFF/invalide × ABI absente/partielle, garde sur les préflights
privés, lecture hors ligne après déconnexion et perte du modèle
en mémoire, politique illisible, réaffectation de session après
observation fournisseur. Vérification qu’aucun état 0.7 n’est lu ni
présenté comme preuve B.

Ce jalon **retire l’autorité positive résiduelle D1 sous CAS** ;
le retrait complet W2 à ABI absente/partielle et la migration des
archives 0.7 ownerless restent dépendants de #3196 et #4858.
