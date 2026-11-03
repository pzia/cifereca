# Core P0 #3719 — downloadable-resource contract v2 (A/Core)

**ABI:** `cifereca.downloadable-resource/2`, publicly exported by
`cifereca.plugin_api` from `downloadable_resources.py`.

## Normative evidence model

| Type | Owner | Meaning | NOT proof of |
|---|---|---|---|
| `SourceReference` | Plugin observes; Core validates | Exact `identity/provider/credential/account/branch/edition/resource/kind` | Legal ownership or local bytes |
| `DownloadCapability` | Authenticated plugin | `available + selected offer ID`, `unavailable` or `unknown`; explicit `archived` | Consent or completed GET |
| `OptionalRightMetadata` | Provider (optional) | Active/inactive/unknown, descriptive entitlement ID, expiry | Independent permission or veto |
| `ResourceObservation` | Plugin | Positive observed resource with evidence reference and timestamp | Acquisition authorization |
| `ResourceSnapshot` | Plugin | Complete/partial/failed observation and pagination | Revocation or absence of missing resource |
| `DeliveryCandidate` | Plugin | One offered candidate path from a Core-chosen staging target | Artifact, placement, Core completion receipt |

**Right metadata may be absent.** An authenticated `DownloadCapability(state="available", offer_id=...)`
stays positive with right state `unknown`, `inactive` or no right object.
Without that exact positive offer there is **no GET**, even when `right.state="active"`.
`archived` is an explicit unavailable delivery fact and is not an error.
`resource_id` identifies a provider's exact resource; it is **not** an inferred
legal entitlement or a bibliographic ISBN.

A plugin cannot acquire, make SQLite changes or issue a `CompletedDelivery`
by constructing these transport objects. The existing Core Sync must still
check current identity/account/session, exact offer, persisted revocable source
consent ON for profile/provider/kind, lock and epoch before GET, then validate
staged EPUB/PDF and SHA, publish OriginLinks/Placement under SQL lock, and
revalidate consent before commit. No per-book consent.

## Explicit compatibility (v1 still supported)

`from_legacy_rights_snapshot()` only promotes provider-response-attested
v1 observations; unattested legacy rows are dropped and completeness is
downgraded to unknown. `as_legacy_rights_snapshot()` projects a v2 resource
into the *existing transport*, mapping `resource_id` to the v1 `right_id`
slot and **not** inventing a legal grant: missing right metadata maps to
`state="unknown"`. These functions do not grant acquisition or imply
`SourceReference` local presence.

**Core integration:** the same Sync executor discovers these optional v2 methods
before their v1 counterparts:

| Stage | v2 method | Result |
|---|---|---|
| Inventory | `downloadable_resource_snapshot(account)` | `ResourceSnapshot` |
| Selected offer | `downloadable_selected_offer_snapshot(account, *, edition_id, resource_id, kind, branch, explicit_user_action)` | One-resource `ResourceSnapshot` |
| Staged candidate | `downloadable_selected_delivery_candidate(account, *, edition_id, resource_id, kind, branch, selected_offer_id, target_dir, explicit_user_action)` | `DeliveryCandidate` |

When the corresponding v2 method is absent, the existing v1 method remains
supported. A v2 exception, malformed result, changed account or mismatched
source/offer never triggers a second call through v1. The v1 inventory reader
may also return `ResourceSnapshot` for transitional providers.

`rights_snapshot_read.py` validates the live identity/credential/account and
converts v2 observations into the existing executor transport under the same
session/epoch checks. The candidate's complete `SourceReference` and selected
offer must match Core's request before Core validates its staging path, format,
SHA and independently witnesses completion. No candidate authorizes publication.
This method discovery does not replace source consent, its revocation checks,
ADEPT binding or the final SQLite guards.

The contract plus Core reader bridge is a milestone, **not** final #3719/#3171
acceptance or validated real Numilog EPUB delivery.

## Extension optionnelle — admission Core vivante pendant la livraison (#3171)

**Aucun changement de signature de l'ABI v2 actuelle.** Un plugin peut
implémenter en plus la méthode optionnelle
`downloadable_selected_delivery_candidate_guarded(account, *, edition_id,
resource_id, kind, branch, selected_offer_id, target_dir,
explicit_user_action, admission_current)`.
Elle retourne le même **DeliveryCandidate** typé et scopé que le lecteur v2.
La découverte est passive (sans appel de propriété Python du provider) ;
**elle est sélectionnée seulement si l'exécuteur Core possède déjà son
lecteur de consentement vivant**. Une méthode absente conserve le v2 existant.
Un adaptateur v2 peut n'exposer **que** ce candidat gardé (ou le candidat
`_runtime_guarded` ci-dessous), sans exposer un GET non gardé : l'activation
du consentement source et le dispatch Core reconnaissent cette ABI, sans
repli implicite sur v1 ou un candidat non sécurisé.
Une exception ou un candidat invalide de cette méthode **n'active aucun
repli** par le v2/v1 historique.

Le callable **admission_current() → bool** est construit par le Core pour
l'offre exacte déjà admise sous verrou. Il compose la garde existante de
liaison identité/credential/compte/session/epoch/SourceKey avec la réévaluation
du **consentement persistant de source et de sa génération initiale** :
ON→OFF→ON ne réautorise jamais un ancien GET. Une exception ou une preuve
non vérifiable retourne False. La fermeture **expire obligatoirement à la fin
ou à l'échec de cette tentative** (et refuse tout appel lorsque le verrou de
Sync n'est plus détenu), même si la politique reste ON. Il est **non sérialisable**, ne confère pas
de droit, ne peut être substitué par `explicit_user_action` ou par
`connected`, et ne doit pas être exposé dans le DTO de retour.

**Obligation de l'adaptateur opt-in :** composer ce callback avec ses gardes
fournisseur (propriétaire, broker, credential, session, staging) pour
`HTTPX_TRANSPORT.attested_https_download(admission_current=...)`, à
**chaque hop HTTPS, bloc réseau et frontière de réception**. Sur False,
interrompre sans GET supplémentaire, sans publication ni retry, et faire
nettoyer le staging privé. Le Core réévalue également au début, après GET,
après ADEPT éventuel et juste avant émission du reçu final ; l'exécuteur
conserve son contrôle SQL transactionnel avant OriginLink/Placement.

**Limite expresse :** l'ancien v2 sans opt-in reste compatible mais ne reçoit
pas cette fermeture Core par bloc. Ce correctif Core ne vaut **ni migration
Kobo/Numilog/BnF/Paris, ni preuve de consentement revérifié pendant leurs
transports effectifs** ; leur rôle Plugins doit adopter l'extension et fournir
les tests d'absence de fuite, de révocation au milieu d'un flux, de refus DNS/
URL dangereux et de non-rejeu. Voir #3848, #3867, #3252.

## Required execution sequence

1. Sync observes and confirms the exact offered resource under global source
   consent; only an exact, verified *same-source* local OriginLink and current
   per-profile file can skip retrieval.
2. Otherwise **GET to isolated staging**; verify final bytes, format and SHA.
3. **Place and publish** the newly received copy with the correct SourceReference
   and all provider/ADEPT OriginLinks, without overwriting other local files.
4. **Only then** run physical SHA deduplication within the same `identity_id`,
   moving/preserving every origin and catalogue/path reference. A dedup failure
   must never invalidate completed acquisition or trigger another GET.

Same SHA in another profile cannot authorize reusing another profile's
path/placement. A verified file's physical integrity, not a historical receipt
or `backup_items`, is the authority for reuse.

**Distinction normative #3171 (01/11/2026) :** le statut de ce snapshot de
**ressources téléchargeables** ne constitue jamais, à lui seul, une preuve
négative sur des achats ou des prêts. Pour les **prêts**, seule la lecture
courante des prêts actifs du compte/branche exact, correctement terminée
(et paginée si nécessaire), permet de marquer un prêt ancien absent comme
**terminé** ; une lecture échouée, partielle ou non exécutée ne le permet pas.
Cette transition préserve fichier, Artifact, Placement, OriginLink fournisseur,
SourceReference et preuves ADEPT. Pour les **achats**, une absence distante ne
révoque rien : la concordance du catalogue personnel est complète lorsque les
références des copies locales attestées L sont toutes dans l'inventaire courant
R du même compte (**L ⊆ R**), sans confondre couverture, résultat de Sync et
publication physique. Voir [ARCHITECTURE.md](ARCHITECTURE.md#11-sémantique-dabsence-et-de-couverture--arbitrage-du-01112026).

## CI

`pytest -q tests/test_core_p0_3719_downloadable_contract_v2.py`

`pytest -q tests/test_core_p0_3719_v2_execution.py`

The fixture matrix includes optional rights with three legal states, available
vs unknown/archived capabilities, 351 references, two accounts, partial pages,
unattested historical rows, corrupted evidence and v1 projection integrity.
It also exercises the real v14 SQLite Core reader/Sync path: absent,
unknown or inactive optional rights plus a current offer produce a verified
synthetic PDF and a provider OriginLink, then an idempotent second Sync.
These fixtures are **not** real supplier EPUB end-to-end proof (#3171/#2797).
The execution matrix also covers v2-only sessions, absence of v1 fallback,
wrong resource/profile/branch/offer and staging paths, source OFF and account
rebind. The installed offline reference provider supplies a real synthetic EPUB
with absent right metadata through its v2 adapter; Core validates and publishes
it, then a fresh Sync epoch reuses its exact placement without another delivery.


## Extension optionnelle — DRM runtime canonique supervisé (#3252, 03/11/2026)

Pour un candidat qui exige un exécuteur DRM plutôt qu'un simple GET HTTP,
le plugin peut exposer
`downloadable_selected_delivery_candidate_runtime_guarded(account, *,
edition_id, resource_id, kind, branch, selected_offer_id, target_dir,
explicit_user_action, admission_current, execute_runtime)`.
C'est un **opt-in distinct** : priorité sur l'extension guarded précédente,
uniquement avec un consentement Core vivant. Les ABI v1/v2 des autres providers
ne changent pas. Tout échec ferme le parcours, **sans repli**.

**Route mixte (Core #3719).** Lorsqu'une même session propose à la fois
des livraisons directes HTTPS/ACSM et des offres DRM, elle peut ajouter le
classificateur local facultatif
`downloadable_selected_delivery_requires_runtime(account, *,
edition_id, resource_id, kind, branch, selected_offer_id) -> bool`.
Il doit être **pur**, sans I/O ni stockage de droits, et décider uniquement à
partir de l'offre opaque **exacte** qui a été attestée puis sera revérifiée
par le provider. `True` impose la méthode `runtime_guarded` et sa preuve
d'exécution Core ; `False` exige la méthode distincte
`downloadable_selected_delivery_candidate_guarded`, avec contrôle vivant
du consentement pendant HTTPS, puis ADEPT Core pour une réponse ACSM.
Classificateur absent : comportement précédent strict, runtime obligatoire.
Exception, type non booléen ou méthode guarded absente : **refus sans repli**
sur l'ABI non gardée. Le Core ne connaît aucun nom de fournisseur pour choisir
la route et le DTO candidat ne peut pas désactiver l'obligation runtime.

Le Core possède seul `execute_runtime(request)`, fermeture **à usage unique**,
valide pour cette tentative exacte sous verrou. Elle exige une recette typée,
une sortie EPUB/PDF neuve dans le dossier de tentative Core, `ok=True`,
un chemin Artifact correspondant exactement à la destination demandée, puis
revalide le consentement. Le Core vérifie ensuite la structure non protégée,
le SHA, la provenance et la transaction finale ; le plugin ne crée jamais B.
Après réussite/échec le callback est périmé et le staging échoué est nettoyé.

Pour Kobo/KDRM, `source.type=kobo` est transmis au service
`runtime.kobo-adept.download_request` installé (contrat public v8, schema v2).
Un candidat `handling=kobo_kdrm` est qualifiable seulement avec la capability
KDRM disponible et opérationnelle, la livraison HTTPS épinglée
`library_sync`, une recette déléguée `content_access_request` **exactement
liée au compte/produit**, et le contexte `device_id/user_id` obtenu par
Vault. Seul Adept rejoue la requête pour obtenir des `ContentKeys`, qui ne
doivent pas apparaître dans les appels provider/Core ni dans les journaux.
Un 403, un compte changé, une recette absente, un consentement révoqué, un
format inconnu ou un runtime manquant **laissent la ressource A en attente**,
sans GET HTTP simplifié, sans suppression et sans prétendre à une livraison.

Cette tranche est testée avec huit achats/KEPUB **synthétiques** et une
publication SQLite v14 isolée. Elle ne constitue pas encore une recette Kobo
réelle, et n'élargit pas les garanties aux variantes KDRM non attestées par
la réponse de Vault. Les couples format de sortie / format source admis
pour la variante KDRM épinglée sont KEPUB/KEPUB, EPUB/FLEPUB (Kobo-Adept
1.3) et EPUB/EPUB ; toute autre divergence reste non qualifiée.
