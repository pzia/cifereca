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
path/placement. Partial inventories never generate negative reconciliation.
A verified file's physical integrity, not a historical receipt or
`backup_items`, is the authority for reuse.

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
