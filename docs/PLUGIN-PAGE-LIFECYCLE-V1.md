# ui.page.lifecycle/1 — notification de présentation optionnelle

`UiPageContribution(..., lifecycle=callback, lifecycle_account_scoped=False)`
est une extension additive du contrat 1.x. Le callback reçoit
`(host, widget, event)`. Le DTO `UiPageLifecycleEvent` est dans
`cifereca.plugin_api.models`; aucun import GTK ou Core privé nécessaire.

- `show` : un rendu local à l'entrée effective dans la route (deux signaux
  identiques ne font pas deux rendus).
- `context_changed` : effacer/invalider immédiatement les sélections et
  résultats précédents. Si visible, projeter uniquement le scope courant ;
  si masquée, ne pas lire ni rendre, attendre le prochain `show`.
- `hide` : invalider les actions et publications du rendu quitté.
- `dispose` : relâcher les handles propres à la page lors du rebuild,
  sans requête ni mutation ; l'ancien événement est déjà périmé.

`event.identity_id`, `generation`, `route`, `visible`, `reason` sont
immuables. `account` contient uniquement les coordonnées fournisseur,
credential ID et compte si la contribution déclare explicitement
`lifecycle_account_scoped=True`; il est absent pour les pages par profil.
Le shell vérifie la génération (y compris A→B→A), le Core propriétaire,
la contribution montée et la visibilité. Avant affichage d'un résultat
différé ou nouvelle action, le plugin doit vérifier `event.is_current()`
sur le thread shell ; un événement périmé ne constitue pas une autorité.
Les tâches métier déjà admises suivent leur propre contexte Core.

Le callback n'est jamais une autorisation réseau, Sync ou acquisition.
Les lectures locales doivent être explicites par identité. Les pages
historiques sans hook conservent leur compatibilité. Le shell ne crée ni
thread, ni abonnement au sélecteur, ni broker supplémentaire. La page Sync
standard consomme cette notification, en conservant ses gardes d'action.
Les autres routes gardent leurs politiques de lecture explicitement
établies ; migration progressive et distincte des routes Notices.

Le présent lot révoque les leases dès fermeture de fenêtre. Le branchement
de la notification de disposition explicite dans `window_close.py` reste
à recevoir après la réservation de ce fichier par #6366/#6743. Aucun
abonnement nouveau à un widget n'est créé par le dispatcher.
