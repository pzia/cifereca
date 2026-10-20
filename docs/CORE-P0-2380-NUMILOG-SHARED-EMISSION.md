# #2380 — émission commune Numilog startup, contrat v1

Propriétaire : Core, `numilog_startup_diagnostics.public_fields`. Le schéma
existant reste la source unique de validation. `StartupTrace.emit` produit une
seule structure validée, puis la transmet indépendamment au collecteur et au
journal INFO existants. Aucun collecteur supplémentaire, changement de niveau,
activation DEBUG, upload ni déclenchement de tâche n'est ajouté.

| Champ | Provenance / validation | GUI locale via events | diagnostics.events / export volontaire | Fichier log de cette émission | CI / IA distante |
| --- | --- | --- | --- | --- | --- |
| `phase`, `cause_phase`, `code`, `status` | Énumérations Core fermées | Oui | Oui | Oui | Même projection, seulement sur transmission explicite |
| `task_kind` | Constante `source-materialize` | Oui | Oui | Oui | Idem |
| `attempted`, `downloaded`, `skipped`, `failed` | Entiers exacts bornés, cohérence des totaux | Oui | Oui | Oui | Idem |
| `task_ref` | Référence aléatoire du contexte réel du worker | Si observée | Si observée | Si observée | Idem, risque de corrélation à considérer |
| `run_ref` | Référence aléatoire de cette exécution de sauvegarde | Si observée | Si observée | Si observée | Idem |
| `provider_id`, durée, `retryable`, HTTP status | Non observés dans ce schéma | Absents | Absents | Absents | Absents |
| Compte, source privée, credential, titre, chemin, URL, exception, XML, token | Aucune publication autorisée par ce contrat | Interdit | Interdit | Interdit | Interdit |

Le journal contient `TRACE numilog startup_materialization ` suivi d'un JSON
canonique borné (2 048 caractères maximum à la lecture). Seul le logger exact
`cifereca` est admis. Le lecteur revalide **tout** le schéma et l'encodage :
champs supplémentaires, clés dupliquées, suffixe, valeurs malformées ou JSON
profond/incomplet ne sont jamais promus en faits structurés. Le message public
utilise le même ordre canonique dans les deux sources, jusqu'à 500 caractères ;
les champs structurés validés restent disponibles indépendamment du rendu.

La corrélation utilise exclusivement les références explicitement émises.
`task_ref` atteste le contexte du worker, `run_ref` une exécution de sauvegarde ;
aucune ne désigne une identité ou un achat. Sans contexte de worker, `task_ref`
reste absent. Une issue, un titre, un instant ou un `event_id` ne remplace jamais
ces références. Les `event_id` restent propres aux sources ; des événements au
même milliseconde ne sont pas ordonnés causalement par leur hash.

Le résultat terminal du worker (`success/failed/cancelled`) et le résultat
métier Numilog (`complete/failed/partial/blocked/not_due`) sont distincts. Un
retour Python normal contenant un échec métier produit légitimement un worker
`success` et un événement Numilog `failed`. Le lecteur ne les fusionne pas.

Couverture : le collecteur est en mémoire, borné à 500 entrées par défaut ; il
peut être désactivé ou filtré. Le lecteur consulte au plus quatre fichiers,
32 Kio et 200 lignes par fichier, avec les états de couverture existants.
Une rotation ou un filtre peut supprimer une observation d'un canal. Les deux
émissions ne sont pas une transaction ; une panne de handler ou de collecteur
ne doit jamais modifier une acquisition. L'absence d'un événement ne prouve
donc pas l'absence de l'opération. La rétention physique du journal conserve sa
politique existante ; ce contrat n'ajoute aucune conservation des références.

La présence d'une source et les liens entre opérations peuvent révéler des
habitudes d'utilisation. Le partage hors poste reste volontaire, avec la même
validation à la frontière du consommateur ; aucune autorisation d'envoi IA
n'est implicite. Un plugin Python exécuté dans le processus n'est pas isolé
comme dans un bac à sable de sécurité. Cette projection ne copie jamais le
reste du journal brut, lequel relève d'autres producteurs.

Preuve : tests synthétiques avec le vrai exécuteur de démarrage Core, SQLite et
worker, acquisition simulée, succès/échec et identité privée synthétique.
Aucun compte réel ni recette terrain revendiqués. L'interface de navigation
par opération, les autres producteurs, le fuseau des anciennes lignes et les
frontières propres aux exports clipboard/IA restent suivis dans #2380.
