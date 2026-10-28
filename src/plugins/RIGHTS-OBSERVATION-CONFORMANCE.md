# Contrat d'observation des droits — matrice fournisseurs P1 #3252

Référence du 28 octobre 2026 : `cifereca.rights.observation/1`, API publique
`cifereca.plugin_api.ProviderRightsSnapshot`. Ce document décrit **les faits
effectivement produits par les adaptateurs installables**, pas le mécanisme
de Sync, ni une certification de compte fournisseur réel.

**Handoff offres de livraison #3252 → Core #3171 :**
[matrice des offres réelles, GET, preuves et limites](RIGHTS-DELIVERY-OFFERS-3252.md).
Les observations ordinaires Numilog/Kobo/Paris ne sont pas des autorisations
de téléchargement ; Numilog dispose d'un préflight ciblé optionnel #3586 et
la qualification de l'action signée BnF est traitée dans #3585.

**Autorité séparée :** A = achat/prêt affirmé par le fournisseur dans un scope
identité/provider/credential/compte/branche/édition/droit ; B = octets
d'artefact vérifiés, SHA final et Placement dans un profil ; C = Notice
bibliographique descriptive. Le Core est **seul** responsable d'admettre
un fait A, de rapprocher des OriginLink attestés et de publier B/OriginLink
atomiquement. L'inventaire ne déclenche ni GET de livraison ni import ADEPT.
La Sync unique réalise la convergence automatiquement lorsque l'offre, le
droit et la configuration l'autorisent ; aucun dialogue de réconciliation
généralisé, aucun veto à partir d'un titre ou d'un ISBN candidat.

| Producteur réel | Source et branche A | Identifiants admis | Type et état rapportés | Couverture | Offre de livraison dans A |
|---|---|---|---|---|---|
| `provider-numilog` | `MesLivres` authentifié ; `meslivres` | `numilog:<scope-credential>:<lid>:<variant-hash>` ; pas de rapprochement avec la fiche commerciale | achat, `active`, preuve `numilog.meslivres` | `partial`, pagination non attestée exhaustive, doublons `(lid, variant)` mis en quarantaine | `unknown`, aucune URL signée dans le snapshot |
| `provider-kobo` | manifeste Vault personnel ; `personal_manifest` | `product_id` authentifié strict, compte exact ; édition = droit dans le contrat v1 | achat `owned=True`, `active` même si archivé ; `owned=False` non promu ; preuve `kobo.personal_manifest` | `partial`, vignettes catalogue et résultats publics non probants | `unknown`, archive ≠ droit de télécharger |
| `provider-paris` | prêts DILICOM ; `dilicom` | `Id` ou `HoldingId` **réel** + `RecordId` ; jamais repli titre/UUID | prêt, `unknown` (aucun état actif inféré) ; preuve `paris.dilicom.listloans` | `partial`, pas d'exhaustivité ; branche SYRACUSE physique séparée. Si DILICOM échoue, la PR #3547 (fusionnée) précise `failed/unknown`. | `unknown`, ni ACSM ni PNB déclenché |
| `provider-bnf-pret` | section `active-loans` du HTML authentifié ; `bnf_html` | `loan_id` + `resource_id` exacts, pas de couverture/titre de catalogue comme preuve | prêt `active` observé dans la section active ; preuve `bnf_html.active_loans` | `partial`, même si la page renvoie 0 prêt | `unknown` ; récupération de jeton séparée, uniquement après action explicitement admise |

Chaque ligne `ProviderRightObservation` rapporte une observation positive
provenant d'un compte lié à une identité ; **aucun état `partial` ou
`failed` n'atteste une absence ou une révocation**. Aucun `download_url`
privé, entitlement reconstruit depuis un ISBN, receipt inventé, copie
d'un autre profil, titre, cache public ou fichier `manual/` ne crée ce droit.
`authorizes_mutation` et `authorizes_negative_reconciliation` restent faux.

## Portée et propriétaires : preuves et limites

- **Numilog** : propriétaire issu du broker Core `identities`, unique pour
  le scope `numilog:<credential-hash>`, avec instance de broker, multiset
  de `AccountRef`, session et objet `Account` revalidés.
- **Kobo** : portée `/account` et manifeste personnel exacts, broker Core
  unique, génération de session et identifiant mutable du compte revalidés,
  doublons de `product_id` exclus.
- **Paris** : emprunteur DILICOM/SYRACUSE unique par session et identité
  broker, vérifié après projection. Le compte principal peut avoir
  `Account.unique_id=""` ; la vue Core `AccountRef.account_id` utilise
  `paris:<credential>:primary` (couvert par PR #3545, fusionnée et validée en CI).
- **BnF** : prêt observé et credential de session exacts, broker Core unique
  pour le nouveau snapshot. Le flux de jeton explicitement déclenché
  vérifie lui aussi les changements de broker, sans autoriser l'acquisition
  par le simple inventaire.

**Preuve de credential, frontière Core #3171 — transition v1/v2 :**
les producteurs Plugins ont désormais adopté de façon **additive**
`AccountRef(scope_version=2, credential_id=...)` dans leurs contrôles du
broker d'identités. Cette preuve distingue plusieurs credentials d'un
même fournisseur partageant un `account_id` amont non vide, et la liaison
est revalidée après I/O. Les adaptations sont fusionnées :
**Paris #3562**, **Kobo #3564**, **BnF Prêt #3565** et
**Numilog #3566** (droits et parcours de livraison concerné).
L'émission du contrat public v2 et le routage Core des acquisitions relèvent
de **la PR Core #3559**, indépendamment de cette réception Plugins.
**Tant que le Core en service n'émet que v1**, son `AccountRef` ne
fournit *pas* de credential attesté : un résultat legacy reste une preuve
d'identité de portée limitée et **ne devient jamais une autorisation de
téléchargement ou une preuve transactionnelle v2**. Un mélange v1/v2
ambigu sur un même compte échoue fermé ; un v2 de credential étranger
n'est pas un candidat à la liaison de ce compte. Des credentials distincts
sans propriétaire attesté ne sont pas rapprochés par nom, ISBN, SHA,
copie de profil ou fichier local. Handoff #3171/#3252 :
**ne pas fermer #3252 sur cette seule compatibilité v2.**

## Conformance synthétique à conserver pour les quatre fournisseurs

| Test de réception | Critère |
|---|---|
| 0 / 1 / N prêts ou achats exacts | seulement les lignes réellement attestées, dans le bon compte et la bonne branche |
| A/B et credential concurrent | jamais de droit attribué à une autre identité ; broker manquant, ambigu ou remplacé = échec fermé |
| Core AccountRef v1/v2 | deux credentials partageant un même ID amont restent distincts en v2 ; credential v2 absent/étranger, version inconnue ou v1/v2 ambigu = échec fermé ; aucun mandat de téléchargement dans le broker |
| Session fermée, compte modifié ou remplacé A→A pendant GET/projection | aucune publication des anciennes preuves ; avant un deuxième GET privé paginé Numilog ou une deuxième branche Paris, stop immédiat si compte réaffecté |
| Doublon d'ID ou édition contradictoire | mise en quarantaine des preuves ambiguës ; autres positifs conservés quand possible |
| Branche qui échoue ou inventaire partiel | échec/partiel **sans preuve négative**, pas de déduction de révocation |
| Archives et notices descriptives | catalogue public, archive, titre, ISBN ou SHA isolé ne servent jamais de droit |
| Livraison et ADEPT | aucun GET signé supplémentaire pendant la capture A ; consentement, staging, validation et SHA final restent séparés |

**Tests de production fournisseur hors réseau :**
`src/plugins/provider-numilog/tests/test_p1_3252_rights_observation_snapshot.py`,
`src/plugins/provider-kobo/tests/test_p1_3252_rights_observation_snapshot.py`,
`src/plugins/provider-paris/tests/test_p1_3252_rights_observation_snapshot.py`,
`src/plugins/provider-bnf-pret/tests/test_p1_3252_rights_observation_snapshot.py`.
Les suites des autres contrats `loan_inventory`, HTTPX et ADEPT sont
indépendantes. Aucun résultat de ces tests ne constitue une attestation
de recette réelle chez le fournisseur, ni une preuve de transaction Core
`Artifact/Placement/OriginLink`.

Propriétaires : Plugins pour les producteurs et leur protocole ;
Core #3171/#3196 pour l'autorisation, le broker versionné, la transaction
et la validation après publication. Pas de nouvelle file de réconciliation.
