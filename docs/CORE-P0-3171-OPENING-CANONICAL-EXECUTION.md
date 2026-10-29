# #3171 — Exécution A→B canonique dans la Sync d'ouverture

## Motivation et périmètre

Sur le **29 octobre 2026 uniquement** (434 événements, heure de Paris),
l'ouverture observe des prêts et inventaires mais ne réalise pas l'acquisition
canonique de droits déjà consentis. La matérialisation Numilog historique
examine ensuite six achats : trois déjà vérifiés localement et trois bloqués
`needs_reconciliation`, sans GET. **Cette PR ne prétend pas résoudre les trois
blocages legacy** ; ils exigent une qualification de leurs reçus, placements,
droits et consentements individuels. L'absence de fichier ou le seul ISBN
n'autorise aucune nouvelle attribution de droit.

## Un seul moteur, une seule transaction de droits

La Sync explicite et la Sync d'ouverture appellent désormais
`_run_observed_canonical_convergence` **dans le même
`sync_coordination.py`**, après l'observation A existante. Le moteur
`qualify_selected_offers → execute_admitted_convergence` et le commit
`Artifact/Placement/OriginLink` sont **identiques** aux appels de Sync
explicite ; aucun scanner, dédoublonneur, ordonnanceur ou ledger parallèle.

La Sync d'ouverture utilise son `InterProcessLock(acquisition_lock_file())`
déjà acquis. Une `ContextVar` **bornée au bloc `with lock`** transporte
uniquement cette référence à l'appel dans le même thread, sans introduire une
variable mutable de service ni réacquérir le verrou. Le pipeline contrôle le
type, le chemin et le descripteur vivant du verrou ; un appel direct à
`_opening_sync_locked` sans lease ne peut jamais télécharger.

**La seule permission d'acquisition** est le grant Core persistant et exact
`SourceKey(identity, provider, credential, borrower, branch, edition, right,
kind)`, conservé en SQLite. Les préférences `kobo_backup.enabled`, le
planning Numilog historique, un catalogue v1 et les métadonnées
bibliographiques n'octroient aucune permission. Le lecteur de consentement est
rappelé avant et après chaque opération ; une révocation pendant la sélection
ou la livraison bloque la publication. La session amont, le droit positif,
l'offre exacte, le prêt, le hash final et la portée de profil sont revérifiés
par le moteur existant. Un fichier local ne suffit qu'avec la preuve de
Placement du profil cible et l'OriginLink exact.

La Sync d'ouverture conserve le compteur `canonical_rights` et ajoute
`canonical_execution` (statuts bornés) ainsi que les acquisitions
physiquement publiées à `new_acquisitions`. Un `failed/blocked` rend la source
partielle, sans effacer de droit ou de référence antérieure.

## Tests synthétiques déterministes

`tests/test_core_p0_3171_opening_convergence_executor.py` exerce :
- une publication sur SQLite v14 et le vrai verrou, suivi d'une seconde
  ouverture avec **zéro** second GET et un Placement unique ;
- un planning legacy sans consentement canonique : zéro offre / zéro GET ;
- la révocation exacte au préflight : ni GET, ni OriginLink, ni Placement ;
- l'absence de lease (appel direct du composant interne) : aucun accès réseau.

La suite existante `test_core_p0_3171_selected_candidate_finalization.py`
conserve les scénarios vérifiant staging, ADEPT, hash et publication.
Les tests utilisent seulement des données synthétiques.

## Réception restante

Exiger PR CI Core/Plugins et installation/rollback **sur le dernier SHA**.
Le journal réel ne fournit aucune preuve d'un GET canonique déjà autorisé sur
un fournisseur vivant ; ne pas déclarer #3171 clos. Examiner séparément les
trois achats Numilog en `needs_reconciliation`, les deux
`cross_identity_loan` et l'attention ADEPT, en préservant les droits.
Aucune opération Plugins B ni Diagnostics C modifiée.

## Bilan avec convergence en attente

Un inventaire canonique `success` ne suffit pas à terminer la Sync : si
`canonical_execution.pending > 0` (par exemple offre ou consentement manquant),
la source et le bilan d'identité à l'ouverture restent `partial`, comme la
Sync explicite. Le compteur `pending` conserve la distinction avec `failed`
et `blocked` ; aucune erreur fournisseur supplémentaire ni mutation de droit
n'est produite. Une acquisition achevée ou une présence attestée sur la Sync
suivante n'est pas rendue partielle par cette règle. Les résultats ignorés
non-erreur, tels les archivés, ne constituent pas un pending implicite.

Preuve : `test_core_p0_3171_pending_sync_outcome.py` reproduit quatre faux
succès sur l'ancien code (explicite/ouverture, sans offre/sans consentement)
puis vérifie `partial`, zéro erreur d'exécution et zéro GET. Le cas positif
vérifie une acquisition puis une deuxième Sync présente, un seul GET et un
Placement conservé. Fournisseurs synthétiques, SQLite et verrou réels.
