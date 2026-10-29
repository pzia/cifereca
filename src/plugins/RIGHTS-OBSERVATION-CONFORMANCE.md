# Contrat d'observation des droits — matrice fournisseurs P1 #3252

Référence du 28 octobre 2026 : `cifereca.rights.observation/1`, API publique
`cifereca.plugin_api.ProviderRightsSnapshot`. Ce document décrit **les faits
effectivement produits par les adaptateurs installables**, pas le mécanisme
de Sync, ni une certification de compte fournisseur réel.

**Handoff offres de livraison #3252 → Core #3171 :**
[matrice des offres réelles, GET, preuves et limites](RIGHTS-DELIVERY-OFFERS-3252.md).
Les observations ordinaires Numilog/Kobo/Paris ne sont pas des autorisations
de téléchargement ; Numilog et Kobo disposent de préflights ciblés optionnels #3586/#3592, et
la qualification de l'action signée BnF est fusionnée via #3585.

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
| `provider-numilog` | `MesLivres` authentifié ; `meslivres` | `numilog:<scope-credential>:<lid>:<variant-hash>` ; pas de rapprochement avec la fiche commerciale | achat, `active`, preuve `numilog.meslivres` | `partial`, pagination non attestée exhaustive, doublons `(lid, variant)` mis en quarantaine | `unknown` au scan normal ; `available` seulement dans le préflight ciblé #3586 sur action signée exacte (et identité #3587), sans exporter l'URL |
| `provider-kobo` | manifeste Vault personnel ; `personal_manifest` | `product_id` authentifié strict, compte exact ; édition = droit dans le contrat v1 | achat `owned=True`, `active` même si archivé ; `owned=False` non promu ; preuve `kobo.personal_manifest` | `partial`, vignettes catalogue et résultats publics non probants | `unknown` en scan ordinaire ; préflight sélectionné #3592 seulement après droit exact et POST Vault explicite, `available` si candidat HTTPS GET unique admissible, puis revalidation de l’offre exacte avant restitution du descripteur, archive ≠ droit de télécharger |
| `provider-paris` | prêts DILICOM ; `dilicom` | `Id` ou `HoldingId` **réel** + `RecordId` ; jamais repli titre/UUID | prêt, `unknown` (aucun état actif inféré) ; preuve `paris.dilicom.listloans` | `partial`, pas d'exhaustivité ; branche SYRACUSE physique séparée. Si DILICOM échoue, la PR #3547 (fusionnée) précise `failed/unknown`. | `unknown`, ni ACSM ni PNB déclenché |

**Paris / multi-identités (#3118) :** les lectures d'owner du broker Core
(`loans`, observations de droits et téléchargement explicite) passent
l'`identity_id` du compte emprunteur exact à `broker.accounts(identity_id)`.
Deux identités possédant le même identifiant de compte Paris ne rendent plus
un propriétaire local ambigu. Les contrôles du credential et de l'instance
active restent obligatoires, et tout changement pendant une lecture refuse
le résultat plutôt que de transmettre des droits entre identités.
| `provider-bnf-pret` | section `active-loans` du HTML authentifié ; `bnf_html` | `loan_id` + `resource_id` exacts, pas de couverture/titre de catalogue comme preuve | prêt `active` observé dans la section active ; preuve `bnf_html.active_loans` | `partial`, même si la page renvoie 0 prêt | `available` seulement si une action signée `reader-acs4` du prêt exact est présente dans le même HTML (#3585) ; sinon `unknown`. GET du jeton distinct et explicitement admis |

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


## Candidats de livraison exacts — réception Plugins #3252 (28/10/2026)

| Fournisseur | Préflight ciblé admis | Candidat finalisable par Core | Épreuve sur comptes/credentials |
|---|---|---|---|
| **Numilog** | `rights_selected_delivery_offer_snapshot` (`meslivres`, `purchase`) avec preuve positive de carte exacte | `rights_selected_delivery_candidate` relit le droit et l'offre opaque ; EPUB direct ou **ACSM brut** sous staging Core (#3616), aucun `import_acsm` legacy en voie sélectionnée | identité Core A via broker unique, compte de session éventuellement `default` (#3612) ; refus A→B/A→A, doublons, credential étranger et offre révoquée |
| **BnF-Prêt** | même interface, `bnf_html`, identifiants resource/loan exacts, action ACS4 active | `download_digital` sélectionné revalide et rend un ACSM candidat dans le staging, non un EPUB final | refus B/A, doublons, compte étranger et offre absente avant GET du jeton |
| **Kobo** | même interface optionnelle depuis #3610, `personal_manifest/purchase`, édition==droit, POST descriptor explicitement admis | `rights_selected_delivery_candidate` depuis #3618 : relecture achat, offre opaque exacte, client HTTP public à peer épinglé, sans redirection ni cookies Vault, staging EPUB/PDF/ACSM ; formats contradictoires refusés (#3621) | fournisseur/credential/compte/broker exacts, A→B/A→A, contenu HTTP faux ou démesuré, offre révoquée ; zéro GET de contenu en refus |
| **Paris** | `dilicom`, prêt/édition exacts, mais `state/delivery=unknown` faute de champ amont attestant une activité | pas d'offre automatique canonique ; action PNB sélectionnée relit DILICOM et refuse le POST d'emprunt fallback si lien courant absent (#3596) | contrôle de compte/session/identité ; pas d'ACSM automatique ni droit fabriqué depuis `EbookUrl` |

**Preuves de merge Plugins :** #3605/#3606/#3609 (BnF/Numilog,
sélection et rebind), #3610/#3618/#3621 (Kobo, offre + candidat HTTP +
contradictions de format), #3612/#3616 (Numilog, identité et ACSM brut)
et #3596 (Paris DILICOM frais), tous fusionnés après CI PR réussies.
La suite fournisseur teste les **sessions réelles sous HTTP synthétique**,
mais ne revendique aucune connexion privée en production ni réception ADEPT.

**Intégration Core vérifiée distinctement :** #3601 (consentement persistant
exact), #3608 (préflight dans la Sync), #3613 (candidate→inspection→
ADEPT éventuel→reçu témoin Core→publication canonique) et #3617
(index descriptif après commit) sont fusionnées avec CI. Le reçu
`core-delivery:` témoigne de l'exécution Core, **pas** d'une quittance
indépendante émise par Kobo/BnF/Numilog. Aucune preuve terrain de
deuxième Sync sans GET réel ni de reprise crash matérielle. Le ticket
#3252 reste P1 ouvert, Core #3171/ACSM BnF #3228 P0 ouverts.

### Numilog — compte Core copié et identité du compte vivant

Pour la **livraison candidate sélectionnée**, le `Account` de session
Numilog peut conserver une identité Core non renseignée, tandis que le
Core transmet une copie défensive liée à A. L'autorité est exclusivement
le **broker** (un seul propriétaire attesté du bon
`provider/credential/account`). Le plugin exige l'égalité des trois
coordonnées fournisseur du compte vivant, accepte son identité seulement
si elle est non renseignée (vide, `None` ou valeur legacy `default`) ou A, puis fige et recontrôle **tous** ses champs et le
broker avant de lancer le téléchargement historique exact. Une identité
vivante B contradictoire est refusée, même si le broker porte A. Une
mutation A→B du compte vivant pendant `library()` stoppe avant GET
du fichier ; aucune propagation à un compte étranger. Cette adaptation
n'autorise ni droit de propriété nouveau, ni consentement implicite.
