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

**Contrat prioritaire du 30/10/2026 (supplante l'ancien veto « droit actif »)** :
la permission d'acquisition est le consentement **persisté** du
profil/fournisseur/type d'acquisition (Numilog achats, BnF prêts, Kobo achats),
ou, en l'absence de commutateur source configuré, l'ancien consentement exact
`SourceKey`. Le commutateur global ON consent aux offres actuellement et
ultérieurement téléchargeables du compte authentifié ; OFF révoque toutes les
acquisitions automatiques nouvelles, même avec un ancien grant individuel.
Pour Numilog, le Core épingle **l'époque CAS de consentement avant lecture**
et la recontrôle après le GET ainsi que sous le verrou SQL de publication :
une révocation suivie d'une réactivation ON durant la même Sync ne réhabilite
jamais une livraison initiée sous l'ancienne époque.
`kobo_backup.enabled` (préférence historique non scoped), un catalogue v1,
un titre, un SHA et un ancien droit SQLite ne constituent jamais une permission.
La lecture de l'autorisation se fait avant/après livraison **et dans la
transaction SQLite** de publication, y compris en cas de révocation pendant GET.
L'état de droit `active/unknown/inactive` reste descriptif : seule une
**offre de fichier réellement disponible et attestée** peut entrer en
acquisition. Identité, credential, compte, branche, édition, session et
licence ADEPT demeurent contrôlés ; présence locale attestée uniquement par
Placement physique vérifié et OriginLink exact.

### Transition BnF-Prêt — ne pas doubler l'acquisition

La politique `bnf_auto` est bien la source de consentement globale pour les
prêts du compte. **Tant que le matérialiseur BnF legacy porte seul la
vérification des associations locales déclarées**, lorsque cette politique
est ON, le coordinateur Core conserve cette route et **n'exécute pas une
seconde livraison canonique pour les mêmes prêts**. Cette exception évite
un double ACSM/ADEPT et protège les compteurs de présence déclarée.
La migration de ces preuves et reçus vers l'unique pipeline A→B reste
un critère **ouvert de #3171/#3196**, pas une preuve d'achèvement.

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

### Fournisseur sans route de livraison

Si le provider émet des droits canoniques mais n'implémente pas la route
optionnelle de livraison, l'absence d'exécuteur n'efface pas les décisions du
plan : un droit actif non satisfait maintient la Sync en `partial`, avec
l'inventaire `success` conservé séparément. Si l'exécuteur existe, son résultat
final remplace cette appréciation préalable : une acquisition achevée ne reste
pas artificiellement en attente à cause du plan calculé avant son GET.

Une référence avec droit `inactive` et **aucune offre téléchargeable**
reste descriptive `inactive`. Une offre authentifiée `delivery=available`,
même si le champ droit vaut `inactive` ou `unknown`, est examinée selon
consentement et présence, comme toute autre offre. Cela ne ferme aucun droit SQLite : seuls les
contrats séparés de preuve négative peuvent le faire. Liste vide, archive
ignorée et historique inactif ne rendent donc pas le bilan partiel à eux seuls.
Les tests de bilan couvrent explicite/ouverture avec et sans route, l'historique
positif conservé et l'absence totale de GET/publication sur ces cas.
