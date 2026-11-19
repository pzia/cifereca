# #4496 — Configuration du nettoyage destructif

Arbitrage propriétaire 19/11/2026 : remplacer le gel fixe par une
préférence Core persistante, désactivée par défaut. Développement et
recette fonctionnelle : opt-in explicite sur configuration et bibliothèque
isolées, avec couverture négative du mode désactivé. Pas d'activation
implicite par build dev, variables d'environnement, ancien plan ou plugin.

Configuration JSON :

```json
{
  "maintenance": {
    "destructive_cleanup_enabled": false
  }
}
```

Seul le booléen JSON true signifie opt-in ; absence ou valeur invalide
signifie refus. La préférence n'est pas une confirmation d'opération.

Cette première tranche ajoute le défaut et la lecture stricte de la
préférence Core, pas de raccord aux writers. Le gel fixe reste effectif
jusqu'à réception du raccord complet. À traiter ensuite :

- broker LibraryMaintenance (plan et run) et superviseur canonique :
  lire la même configuration Core actuelle, jamais la valeur d'un plan ;
  vérifier à nouveau avant les mutations et refuser tout plan révoqué ;
- ne pas réactiver par défaut l'ancien delete_duplicate fournisseur
  hors contrat commun ; qualifier ses gardes/confirmation/verrou avant
  toute admission, conserver les exclusions et le postcheck ;
- réglages avancés Core, avertissement et consentement explicites,
  sauvegarde de la préférence et journalisation non sensible ;
- tests fonctionnels avec vraie configuration temporaire true au lieu
  du monkeypatch de gel, tests false/valeurs invalides, stale plan,
  révocation, traversée plugin et récupération de tombstone existant ;
- inventorier séparément les autres writers de suppression ou purge
  (variantes, staging, archives), sans leur accorder implicitement ce droit.

Une activation de test ne touche jamais une bibliothèque existante.
L'autorisation propriétaire d'un déploiement mutateur réel reste
distincte de la recette technique.
