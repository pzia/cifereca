# Observations Numilog et présence locale (#3171, #2797, #3252)

L'attestation du propriétaire passe explicitement l'identité du compte de
session à `IdentityBroker.accounts(identity_id)`. Sans argument, le broker
utilise le profil affiché : ce choix ne doit jamais déterminer les observations
ou le profil de fulfillment d'une synchronisation en arrière-plan. Une session
sans identité attestable ne peut autoriser ni observation typée ni fulfillment.
L'inventaire legacy reste consultable sans promouvoir ses résultats en droits.

Le fournisseur émet les identifiants exacts MesLivres : credential → scope
SHA-256 tronqué, `lid`, empreinte de la variante. `library().record_id` et
`rights_observation_snapshot().right_id/edition_id` conservent la même clé.
La lecture reste partielle et positive, sans GET de livraison. Aucun chemin
local n'entre dans ces identifiants ni dans le choix des achats observés.

Le journal fourni du 29/10 (06:59–07:02 Paris) porte une seule opération
terminale Numilog : six achats examinés, trois `already_local_verified`, trois
`needs_reconciliation`, `preflight_failed`, zéro tentative/acquisition. Ce sont
des décisions Core ; elles ne démontrent pas trois achats absents du fournisseur.
Dans `numilog_purchase_preflight`, des reçus existants ou un conflit de placement
produisent `needs_reconciliation` ; `numilog_materialization_startup` ne transforme
cette décision en présence vérifiée que si le reçu exact, le placement et le SHA
courant concordent. Le journal n'indique pas lequel des contrôles a échoué pour
chaque achat. La base et les reçus ne sont pas joints à cette recette.

Handoff A : rapprocher les six références avec les reçus/OriginLink et placements
de l'identité cible à leurs chemins actuels, sous-répertoires compris ; appliquer
la politique canonique d'acquisition autorisée si aucune preuve locale ne suffit.
Le provider ne scanne pas les fichiers, ne déduit pas une origine du chemin et
ne crée aucun moteur de réconciliation/SHA. Les tests synthétiques couvrent les
six références avec trois fichiers à la racine et trois en sous-répertoires,
trois profils/sélecteurs et les vrais refus de propriétaire/credential.

## Conformance avec le moteur unique

`tests/test_six_purchase_core_conformance.py` exerce le vrai adaptateur Numilog
(parseur MesLivres, offres ciblées et téléchargement EPUB), le broker public,
LibraryService et SQLite. Seul le transport HTTP est simulé. Les six EPUB ont
des contenus distincts ; trois sont à la racine `numilog/`, trois dans des
sous-répertoires. Le sélecteur UI change à chaque réponse HTTP.

Les mêmes cas passent par Sync individuelle et globale : six origines exactes
donnent zéro GET de contenu sur deux Sync ; trois fichiers scannés sans origine
fournisseur ne donnent aucun GET sans consentement ; avec consentement exact,
les trois acquisitions ciblées réutilisent les six Artifacts au SHA final,
conservent les neuf Placements et les six fichiers d'origine, puis la deuxième
Sync ne retélécharge rien. Le provider ne publie ni SHA ni OriginLink : ces
assertions observent la publication du Core existant. Aucun ADEPT ni compte
réel n'est employé ; ce résultat ne certifie pas la réparation de la base terrain.
