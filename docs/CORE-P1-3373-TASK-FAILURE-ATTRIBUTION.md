# CoreUI/Diagnostics P1 #3373 — identifier la tâche fautive au démarrage

## Correction instrumentale

`TaskRunner` dispose déjà du `task_id`, du `kind` validé et de l'`origin`
fermé de chaque tâche. Une exception non typée présentait pourtant la boîte
« Une erreur est survenue / Traitement indisponible », indistincte d'une
synchronisation fournisseur voisine pourtant réussie.

Pour toute **erreur de worker** ou **rejet d'enqueue** de ce runner, un événement
local corrélable est publié :

`TRACE task failure id=N kind=... origin=... phase=worker|enqueue exception=TYPE cause=TYPE|none`

Les types sont des noms Python validés et bornés; ni libellé UI ni
message d'exception contenant potentiellement un bearer/password/cookie
ne sont repris. Ce diagnostic complète les traces `submit/begin/end/outcome`,
sans associer artificiellement l'échec à un provider, une connexion, ou
un `task_ref` inexistant.

Pour `origin=automatic`, et **uniquement en absence de gestionnaire d'erreur
spécifique**, l'échec reste visible dans l'historique d'activité mais
n'ouvre plus une boîte générique bloquante. Les actions déclenchées par
l'utilisateur, les `UserFacingError` et les callbacks `err` métiers conservent
leur comportement. Les succès de tâches voisines ne sont pas modifiés.

## Recette synthétique / limites

- Tâche d'ouverture échouée + tâche `source-sync` voisine réussie :
  événements et identifiants distincts, pas de modal générique automatique.
- Rejet d'enqueue : activité équilibrée, diagnostic `phase=enqueue`,
  contrôle GTK rétabli selon le garde existant.
- Action utilisateur échouée : modal historique maintenu.
- Aucune requête fournisseur, écriture d'inventaire, acquisition ou release.

**Reste pour clôturer #3373** : confirmer sur la version installée la nouvelle
occurrence utilisateur, y compris la frontière des callbacks GTK et leur
propre diagnostic. Ce correctif n'attribue **pas** rétrospectivement
l'incident observé le 26 octobre à Numilog ou Kobo, dont les deux
synchronisations étaient réussies dans l'extrait disponible.
