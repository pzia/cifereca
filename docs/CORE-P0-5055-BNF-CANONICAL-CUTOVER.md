# P0 #5055 — extinction du worker d'acquisition BnF en Core

## Suppression

Le chemin dédié `core/bnf_auto_materialization.py` est **supprimé**.
La synchronisation normale et celle d'ouverture ne doivent plus l'appeler.
Un prêt BnF déjà existant entre exclusivement dans la même convergence
`SourceReference → OriginLink → Artifact/SHA → Placement` que les autres
providers, via l'offre actuelle attestée par la session BnF. Le provider
reste seul responsable de ses requêtes HTML/SSO, sélection d'offre ACS4,
GET signé et interprétation de la couverture partielle du compte.

## Consentement transitoire, sans pseudo-droit

Les installations qui avaient activé explicitement
`sync.source.bnf_auto.<source-id>` conservent **le choix de consentement
à la source**, non les anciens reçus auto, URL/entitlements ni « propriété ».

Le pont `source_auto_consent_bridge` admet ce choix dans le moteur canonique
seulement si les quatre coordonnées identité/provider/credential/compte
correspondent, si ON et son **identifiant de génération** hexadécimal ont été
écrits, et si les références de l'inventaire courant sont attestées. Le
moteur fabrique un ensemble **éphémère** de SourceKey exacts à partir de la
seule observation courante ; aucun droit n'est reconstruit en SQLite.
Un consentement générique `source_sync_consent` configuré (ON ou OFF)
supplante toujours le vieux commutateur.

Avant et après offre/GET, puis pendant le commit SQLite, une révision
identique est obligatoire. ON→OFF→ON change de révision et révoque
l'ancienne opération. Une révision absente/illisible interdit toute
conversion silencieuse vers ON. Une préférence désactivée interdit le GET,
mais n'efface ni origine ni fichier.

## Invariants

- Lecture catalogue et observation de prêts sont séparées de l'acquisition.
- Seuls prêts/éditions et offres **actuellement observés** sont candidats ;
  un inventaire partiel BnF n'autorise aucune clôture par absence.
- La synchronisation ne soumet jamais de nouvel emprunt ni GET de
  confirmation potentiellement mutateur ; elle livre au plus une occurrence
  déjà attestée, si le provider autorise son téléchargement.
- Une chaîne locale Artifact/OriginLink/Placement physiquement vérifiée
  interdit un nouveau GET ; un titre, un OPF, une déclaration manuelle,
  un ACSM isolé ou un reçu du vieux worker ne prouvent pas l'artefact.
- Le Core garde le verrou, la provenance, la validation SHA finale,
  ADEPT, les placements propres à chaque profil et le commit atomique.
  Aucun fichier utilisateur n'est supprimé automatiquement.

Les tests du vieux worker et de sa « réconciliation » titre/OPF sont
supprimés, remplacés par les tests génériques de preuve locale et par les
tests d'intégration synthétiques de consentement BnF→canonique, de
non-répétition de GET et de révocation au commit. Les tests de prêt
explicite restent indépendants et ne sont pas supprimés.

Références normatives : `docs/ARCHITECTURE.md` et
`docs/PROVIDER-DEVELOPMENT.md`.
