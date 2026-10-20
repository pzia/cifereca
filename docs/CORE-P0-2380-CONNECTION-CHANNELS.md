# #2380 — échecs de connexion fournisseur, deux canaux locaux

**Contrat fermé v1 (Core).** `connect_configured` classe chaque tentative
dans `public_connection_failure` après lecture du registre Core ; seul
`Diagnostics.add_core_provider_connect_failure` atteste l'échec pour le
snapshot. La connexion inconnue publie `source_inconnue` et le code
`provider_not_registered` ; une erreur de connexion attestée publie
`provider_connect_failed`. Le compte configuré, l'alias et l'identité ne
sont jamais utilisés comme `provider_id`.

| Fait | `core_events` | `app_log` | Export/CI/LLM |
| --- | --- | --- | --- |
| `provider_id` du registre ou `source_inconnue` | Oui, provenance Core réservée | Oui, JSON canonique sur logger exact `cifereca` | Même projection fermée, partage volontaire uniquement |
| Code `provider_connect_failed` / `provider_not_registered` | Oui, décision Core | Oui, même code | Même règle |
| Statut `error`, message fixe « connexion fournisseur échouée » | Oui | Oui | Même règle |
| Durée locale de la tentative | Dans l'enregistrement Core, bornée | **Non** dans la projection publique v1 | Ne pas l'inventer |
| `task_ref`, succès/failure du worker, origine du prêt | **Non attestés** par cette émission | Absents | Absents |
| `credential_id`, identité/compte, exception, token, URL, chemin, label | Interdits | Interdits dans la trace partagée | Interdits |

Le texte du log canonique a le préfixe `TRACE provider connection failure `
suivi d'un JSON de exactement deux champs `code` et `provider_id`,
sans espace ajouté, suffixe, répétition de clé ni valeur libre. Le lecteur
rejette une autre provenance de logger ou une ligne altérée : pas de
réparation heuristique à partir de `TRACE provider connect error`
historique, même si ce dernier contient le slug en texte. Le producteur Core
valide les valeurs **avant** émission ; le parseur fermé défend la lecture
contre les traces malformées, sans constituer une signature cryptographique
du code Python tournant dans le même processus.

La collecte et le journal restent indépendants : un collecteur désactivé
n'efface pas une trace de log disponible, un handler en panne ne doit pas
modifier la décision de connexion. Des filtres/rotations peuvent rendre un
canal incomplet ; n'inférer aucune observation manquante. Les `event_id`
sont locaux à leurs sources et les échecs répétés du même fournisseur sont
distincts ; aucune corrélation par horodatage ni création de `task_ref`.

Preuves synthétiques : connexion singleton et compte classique, plusieurs
comptes du même fournisseur, plugin absent, collecteur désactivé, log partiel,
mauvais logger, doublon JSON, suffixe privé, types hostiles, exception et
handler défaillants. Aucun compte réel, socket ou nouvelle collecte activée.
