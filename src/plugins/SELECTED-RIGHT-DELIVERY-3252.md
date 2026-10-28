# Plugins P1 #3252 — sélection exacte de l'offre de livraison (tranche BnF/Numilog)

Contrat de transition **optionnel, fournisseur-neutre côté sessions**, pour
l'intégration à Core #3171. Il **ne remplace pas** `cifereca.rights.observation/1`
et ne modifie pas `cifereca.plugin_api` sans accord Core. Il ne constitue
ni une admission, ni une opération de téléchargement, ni un reçu.

## Signature identique

```python
session.rights_selected_delivery_offer_snapshot(
    account,
    edition_id=source.edition_id,
    right_id=source.right_id,
    kind=source.kind,
    branch=source.branch,
    explicit_user_action=True,
) -> ProviderRightsSnapshot
```

L'appelant Core sélectionne **un seul** `SourceKey` après validation
identité × fournisseur × credential × compte × branche × édition × droit × type
et **consentement persistant déjà autorisé**. Le booléen
`explicit_user_action` est un garde défensif d'appel explicite, **pas**
une attestation de consentement ; l'adaptateur ne lit jamais la base Core.

L'adaptateur exige la branche/type qu'il atteste :
- **Numilog** : `meslivres`, `purchase`, `edition_id == right_id`.
  Il appelle le préflight ciblé existant, qui lit l'inventaire positif puis
  la carte achetée signée ; zéro GET de contenu. La portée d'identité est
  revalidée après la dernière lecture.
- **BnF-Prêt** : `bnf_html`, `loan`, identifiants de prêt et ressource
  exacts. Il réutilise une lecture `active-loans` avec action ACS4 éventuelle,
  sans GET du jeton. Le résultat ne conserve que le droit sélectionné.

Les deux renvoient un `ProviderRightsSnapshot` positif, partiel,
`authorizes_mutation=False` et
`authorizes_negative_reconciliation=False`, avec au plus une observation.
L'offre reste `unknown` si la preuve de l'action exécutable n'existe pas ;
un droit non observé/refus de portée produit une exception, **jamais**
un droit inventé. Les URLs signées ne sont pas exposées.

## Garanties et limites

L'offre opaque observée doit être revalidée **à l'exécution** par
`download_direct(...,expected_offer_id,expected_identity_id)` pour Numilog
ou `download_digital(...,explicit_user_action=True,expected_offer_id)`
pour BnF. Ces points d'entrée existants ne partagent pas encore une ABI
de résultat final : Numilog peut produire un EPUB/ACSM via broker et BnF
un candidat ACSM ; aucun n'atteste seul le fichier final post-ADEPT.

**Handoff A/Core :** choisir et fixer la surface publique d'exécution,
avec Core seul responsable du verrou, de l'admission, de la révalidation
du consentement sous transaction, de la finalisation DRM/ADEPT, du
`CompletedDelivery` et de la publication atomique
`Artifact+Placement+OriginLink` au SHA final. Le nouveau point d'entrée
est une implémentation adapter seulement, à raccorder après accord explicite
sur l'ABI publique. Ne jamais faire un second processus de rapprochement.

**Preuves automatisées Plugins :** fixtures privées synthétiques, sélection
dans 0/1/N droits, mauvais `kind/branch/edition/right`, refus sans option
explicite avant réseau, A→B après réseau, état `unknown` et absence de
jeton dans les snapshots. Elles ne prouvent pas une connexion fournisseur
réelle ni un second Sync à zéro GET. Paris reste `state=unknown`,
`delivery=unknown` ; Kobo est en PR de garde DRM/projection concurrente.


## Exécution d'un candidat sélectionné (facette facultative)

Les deux mêmes sessions BnF/Numilog proposent également, **sans
modifier le contrat public Core**, la forme optionnelle suivante :

```python
session.rights_selected_delivery_candidate(
    account, edition_id=source.edition_id, right_id=source.right_id,
    kind=source.kind, branch=source.branch,
    delivery_offer_id=right.delivery_offer_id,
    target_dir=core_staging_dir, explicit_user_action=True,
)
```

Cette méthode n'est appelable qu'après le préflight/admission et le
consentement persistant exact gérés par Core. Elle vérifie les coordonnées
complètes (compte, credential, identité, type, branche, édition et droit)
et **rejoue les contrôles fournisseurs existants** : Numilog
`library() → download_direct(..., expected_offer_id, expected_identity_id)`,
BnF `loans() → download_digital(..., expected_offer_id,
explicit_user_action=True)`. Un changement de propriétaire, un droit
disparu ou une offre signée supprimée/refusée arrête la livraison ;
aucune autre édition n'est choisie par titre.

**Résultat :** la paire historique `(path, source_url)` est seulement un
**candidat d'acquisition**, non un `CompletedDelivery` du Core. Pour BnF,
`path` correspond à un ACSM *à traiter* ; pour Numilog, le chemin peut
être un EPUB validé par le flux existant ou un résultat de fulfillment.
La provenance du jeton privé ou la présence d'un path ne remplacent
pas un reçu indépendant post-GET et le SHA **final** après traitement.
Il reste obligatoire d'inspecter, rehasher et publier côté Core selon
#3171. Ne pas brancher directement ce résultat sur le ledger canonique.

Tests provider-only : refus avant lecture privée sur portée/action fausse,
changement de propriétaire avant/après lecture, disparition de l'offre
signée, invocation d'un seul chemin de livraison explicite. Aucune
attestation de recette live ni de zéro GET au deuxième Sync n'est revendiquée.

## Kobo — préflight de sélection identique (Plugins #3252)

`provider-kobo` offre désormais la **même signature** de
`rights_selected_delivery_offer_snapshot(...)`, avec la branche exacte
`personal_manifest`, le type `purchase` et
`edition_id == right_id == product_id`. Elle réutilise **uniquement**
`rights_delivery_offer_snapshot` : manifeste privé positif puis
POST Vault `descriptor` pour ce produit précis, sans GET de fichier.
Un mauvais choix, l'absence d'action explicite ou une identité non liée
arrêtent la demande **avant tout réseau**. Une seconde garde rejette le
rebind A→B/A→A, la modification du compte et de la session pendant la
projection du `ProviderRightsSnapshot`.

Les sécurités de #3595 (propriétaire après projection) et #3602
(cohérence des drapeaux DRM) sont fusionnées. Le résultat reste
`available` ou `unknown` suivant les preuves du descripteur ; pas
de déduction d'acquisition depuis `downloadable` ni depuis une archive.

**Limite :** Kobo n'implémente pas ici
`rights_selected_delivery_candidate`. `resolve_acquisition` produit
un **descripteur privé**, pas des octets vérifiés : son URL ne doit jamais
devenir un reçu Core. Le GET de contenu, la validation du format/DRM et
le reçu indépendant demeurent à raccorder sous le contrôle de A/#3171.
Paris reste `state=unknown`, donc pas de promotion en droit actif
pour une Sync automatique.
