# Notice C — ABI d'exécution des consolidateurs texte (P1 #4981)

## Portée et identité

La sélection Core est explicite par `(canonical_type, scope, plugin_id, version,
schema_id)`. Le registre entier est validé, y compris les déclarations non
sélectionnées. Aucune sélection par ordre d'enregistrement, version la plus
récente ou nom fournisseur. Le registre des schémas canoniques est contrôlé
indépendamment avant toute exécution.

L'API publique Core `run_registered_text_consolidator` reçoit les déclarations,
le schéma installé, une liaison `InstalledTextConsolidator` *provenant
exclusivement de l'inventaire de plugins installé et vérifié par Core*, les
faits sources descriptifs, l'édition cible et les preuves de liens forts
attestées par Core. Les données fournisseur ne peuvent créer ou choisir une
liaison installée. `source_sha256` épingle le contenu de code déjà validé par
Core ; le programme reçoit uniquement le sous-ensemble de faits admis, sans
objet `LibraryService`, credential, objet HTTP, chemin bibliothèque ou
connexion SQLite.

## Contrat de la fonction plugin

Le script Python installé définit une unique fonction pure
`consolidate(facts)` et ne dépend d'aucune importation. `facts` est un
mapping JSON contenant `canonical_type`, `schema_id`,
`target_edition_id`, `exact` et `inherited`. Chaque fait comporte
`source_id`, `edition_id`, `evidence_id`, `canonical_type`,
`source_revision`, `value`, `relation`, `inherited` et
`relation_proof_id`. Les listes sont bornées, triées et dédupliquées par Core.

La sortie est `None` (non connu) ou **exactement** les clés
`canonical_type`, `target_edition_id`, `schema_id`, `value`,
`inherited`, `source_evidence_keys`. La dernière clé contient des triples
`[source_id, edition_id, evidence_id]` se référant aux faits admis.
L'héritage ne peut supplanter une preuve exacte. Les règles métier du type
(catégorie d'âge, série, langue, etc.) sont entièrement dans le plugin ; Core
ne choisit aucune valeur.

## Isolation et réversibilité

Le worker est un processus Python isolé (`-I`), lancé avec environnement
minimal, sans import de module plugin depuis le disque. Le code du
consolidateur est compilé et exécuté **après** activation d'un filtre seccomp
Linux. Les ouvertures de fichiers, connexions réseau, lancements de processus,
mutations FS et accès SQLite sur fichier sont interdits au niveau noyau ; les
builtins d'importation, d'ouverture, d'évaluation et de réflexion ne sont pas
exposés. Un Linux sans libseccomp, une erreur de configuration du filtre, une
exception plugin, un crash ou une expiration temporelle donne un refus fermé,
jamais une exécution non confinée. Budgets : script 32 Kio, requête 2 Mio,
réponse 16 Kio, 3 s par invocation, CPU worker 2 s, pas de sortie de logs privés.

Chaque résultat est recalculé dans un second worker frais ; des sorties
différentes font échouer le lot. **Core revérifie lui-même** le contexte
d'édition, la lignée des faits, les références, les limites du schéma, les
enums et l'héritage après exécution. Ni publication SQLite ni index canonique
n'est effectuée dans cette API. L'écriture persistante et l'ordonnancement
restent les responsabilités séparées des tickets #2385 et #4982.

La liaison doit être **provenant de Core** : le hash du code garantit
l'immuabilité entre déclaration et invocation mais ne transforme pas un plugin
non approuvé en code fiable. Le worker refuse toutes les plateformes où
l'isolation seccomp ne peut être installée. Les preuves synthétiques ne
remplacent pas une recette fournisseur réelle et n'autorisent aucune mutation
de droits.

## Validation

`pytest -q tests/test_core_p1_4981_consolidator_worker.py` :
sélection/version concurrente, faits exacts/hérités, collisions, portée
étrangère, schéma absent, provenance forgée, sortie invalide, absence de
permissions IO, limites, timeout, double exécution déterministe.
