# Provider de référence — rôle B (#3722)

Ce package simule un fournisseur payant **exclusivement hors réseau**. Il est
désactivé par défaut. Il n'utilise aucun compte ni contenu de production et ne
doit pas être présenté comme un fournisseur commercial.

## Frontière et API

| Étape | Producteur B | Consommateur Core A |
| --- | --- | --- |
| 1. Comptes | `connect()` rend un/deux `Account` fictifs | Lie identité, credential, compte et consentement |
| 2. Inventaire | `rights_observation_snapshot(account, resume_cursor="")` | Sélectionne les `SourceReference` et traite les pages |
| 3. Offre sélectionnée | `rights_selected_delivery_offer_snapshot(..., explicit_user_action=True)` | N'appelle qu'après autorisation exacte ; `unknown` ne prouve rien |
| 4. Livraison sélectionnée | `rights_selected_delivery_candidate(..., target_dir, delivery_offer_id, explicit_user_action=True)` | Crée le staging privé, puis valide EPUB/DRM/SHA |
| 5. Publication | **Aucune méthode provider** | Seul le Core publie Artifact, OriginLink, Placement, journal et reprise |

L'API consommée aujourd'hui est `cifereca.rights.observation/1`, avec la
facette optionnelle `rights_selected_delivery_*()`. Le contrat de référence
`SourceReference/DownloadCapability/OptionalRightMetadata/DeliveryCandidate`
à venir relève de **#3719** (Core). La migration s'effectuera sur cette
branche B quand l'ABI A sera figée, **sans** implémenter un second moteur et
sans faire importer des modules `cifereca.core` ou `cifereca.storage` au
provider.

Le broker public `identities` exige un unique `AccountRef` v2 dont
l'identité, le credential et le compte sont identiques à la session. Un compte
copié de même scope est accepté ; les comptes étrangers, credentials non
qualifiés, références v1 ambiguës, changements de session et offres obsolètes
sont refusés. Le texte de l'offre contient uniquement un hash opaque attaché
au scope exact et à l'époque de session.

La liste n'implique **aucune** présence locale : ni ISBN, titre, droit,
chemin, SHA, autre profil ni reçus antérieurs ne justifient une liaison.
`delivery=available` est observé indépendamment de `state`. L'inventaire
reste `partial` même lorsqu'une page indique la fin de pagination :
**aucune preuve négative** n'en découle.

## Scénarios

- `mixed` : deux achats positifs dont un avec droit inconnu ; un prêt positif
  avec droit inconnu ; droit déclaré avec capacité inconnue ; refus explicite ;
  archive ; deux livres ayant le même SHA EPUB ; une livraison interrompue une
  fois, puis disponible à la reprise.
- `empty` : zéro ressource ; aucune absence ou révocation n'est affirmée.
- `bulk` : 1 à 500 références (350 par défaut), pages de 1 à 100, curseurs
  déterministes, aucun résultat privé non sélectionné.

Pour les comptes `reader-a` et `reader-b` (option `accounts=one|two`), les
données et EPUB sont synthétiques. Les archives ne deviennent jamais
téléchargeables par leur simple présence ; une capacité inconnue ou refusée
n'est jamais un substitut d'autorisation. La séquence de même SHA sous deux
comptes sert à vérifier que Core **ne transfère pas les permissions**.

## Vérifications

```bash
PYTHONPATH=src:src/plugins/provider-reference python -m pytest -q \
  src/plugins/provider-reference/tests/test_reference_provider.py
```

Les tests B vérifient le contrat et la livraison d'EPUB isolés, les scopes, la
pagination, la reprise après interruption, les archives, l'absence de
remplacement et l'absence d'accès SQL. Les tests de Core #3719 doivent
vérifier séparément consentement ON/OFF/révocation, local exact, Artifact
global, SHA final, publication atomique OriginLink/Placement, redémarrage,
traitement de 300+ lignes et absence d'acquisition double.

**Limites de réception** : ni preuve E2E du moteur Core sur ce provider, ni
compte réel, ni comportement ADEPT/KDRM. Aucune action utilisateur réelle
requise pour ce banc d'essai.
