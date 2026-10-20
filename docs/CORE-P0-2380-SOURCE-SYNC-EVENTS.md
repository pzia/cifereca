# #2380 — événements métier de synchronisation de notices, v1

Propriétaire : Core. `synchronize_account_source` émet une décision de début
puis une décision terminale via `Diagnostics.add_core_source_sync`. Le
validateur `core/diagnostic_source_sync.py` est commun au collecteur et à la
lecture du journal. Aucun nouveau collecteur, logger, appel réseau, upload ou
mode DEBUG. Les événements n'autorisent aucune acquisition et ne remplacent
ni le résultat de synchronisation ni le journal d'actions existant.

| Champ | Preuve | GUI locale / diagnostics.events | Export support / copie explicite | Fichier log de cette émission | CI / IA distante |
| --- | --- | --- | --- | --- | --- |
| `schema_version=1`, `operation_kind=source_sync` | Producteur Core dédié | Oui | Oui | Oui | Même projection, transmission explicite seulement |
| `provider_id` | Identifiant exact du registre Core puis validation de slug | Oui | Oui | Oui | Idem |
| `phase`, `status`, `code` | Couples phase/statut et codes fermés | Oui | Oui | Oui | Idem |
| `duration_ms` | Temps monotone entier, 0–86 400 000, terminal uniquement | Si observée | Si observée | Si observée | Idem |
| `loans`, `library` | Compteurs entiers observés, 0–1 000 000 | Si observés | Si observés | Si observés | Idem |
| `task_ref` | Contexte réel du worker, référence aléatoire créée à la première émission | Si contexte disponible | Si publiée par le consommateur | Si contexte disponible | Idem |
| Code fournisseur, HTTP status, `retryable` | Non attestés par ce producteur | Absents | Absents | Absents | Absents |
| Compte, identité, source privée, titre, chemin, URL, token, exception, XML | Données non admises par ce schéma | Interdit | Interdit | Interdit | Interdit |

Les codes sont `source_sync_started`, `source_sync_complete`, `source_disabled`,
`source_sync_failed`, `operation_busy` et `source_sync_cancelled`. Un fournisseur
non enregistré ou dont le slug n'est pas publiable devient `source_inconnue`.
Un plugin qui appelle le générique `Diagnostics.add` avec les mêmes noms de
champs n'atteste **pas** un événement : le collecteur réserve un marqueur au
producteur Core, revalidé à la projection. Un plugin Python en processus
n'est toutefois pas un bac à sable de sécurité contre du code hostile.

Le log contient `TRACE source sync ` puis le JSON canonique. Seul le logger
exact `cifereca` est accepté, avec limite de lecture 2 048 caractères et
revalidation complète du schéma. Aucun suffixe, clé dupliquée, objet arbitraire,
compteur booléen ou code libre n'est admis. Le message public et le détail
validé sont identiques dans les deux canaux ; les `event_id` restent locaux.

La référence désigne un contexte d'exécution de tâche, jamais un compte.
Deux tâches concurrentes ou successives sur un worker réutilisé ont des
références distinctes. Un appel hors worker n'a pas de `task_ref`. Le nom
historique `numilog_task_reference` reste compatible avec la primitive commune
`diagnostic_task_reference` ; aucun nouveau contexte parallèle n'est créé.
La vue Diagnostics peut utiliser ces références publiées, sans rapprochement
par heure, titre ou message. Une décision de sauvegarde Kobo `partial` et une
synchronisation de notices `success` restent deux faits indépendants.

La collecte mémoire conserve ses filtres et son plafond de 500 entrées par
défaut ; le journal conserve ses niveaux et sa rotation existants. Le lecteur
reste limité à quatre fichiers, 32 Kio et 200 lignes par fichier. Un filtre,
une rotation ou une panne peut rendre une source partielle. Désactiver le
collecteur ne force pas l'arrêt du journal, et une panne de télémétrie ne
change jamais le résultat, le verrou ou l'annulation de la synchronisation.
Les événements ne sont pas une transaction entre canaux ; ne pas promettre
un historique complet ni une rétention supplémentaire.

Le partage d'une référence et de la présence d'un fournisseur peut révéler
des habitudes d'utilisation : conserver le consentement de transmission et
les limites des consommateurs, notamment sélection/copie et IA. Les exports
ne doivent pas ajouter de champs privés pour compenser une observation absente.
Le contrat ne donne aucune permission supplémentaire de publication CI/LLM.

Preuves synthétiques : vrai coordinateur Core, SQLite, verrou et worker,
session sans réseau ; succès, source désactivée, échec, annulation, verrou
occupé, provenance absente, concurrence et réutilisation du worker, refus de
faux événements et trace hostile. Aucune recette fournisseur réelle ou GTK
nouvelle revendiquée. Les autres producteurs et la normalisation UTC des
anciennes lignes restent suivis dans #2380.
