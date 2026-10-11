# Recette UX Diagnostics P0 — scénario GTK4 isolé

Commande (sur environnement Debian 13 du dépôt, GTK4/PyGObject disponible) :

```bash
dbus-run-session -- /usr/bin/python3 e2e/run.py --scenario diagnostics-1366
```

Le scénario utilise le runner E2E **existant** et son HOME/XDG temporaire, Xvfb
1366 × 900, ainsi que le **vrai code GTK du plugin** `ui.page.diagnostics`.
Les brokers de diagnostic et de tâches sont simulés par des données déterministes
anonymisées ; aucun compte, provider, LLM ni réseau n'est autorisé.

Preuves attendues : `e2e-artifacts/diagnostics-1366/screen.json`, avec état
`PASS`, 4 captures `00-diagnostics-loaded.png`,
`01-report-expanded.png`, `02-structured-json.png` et
`03-journals-second-page.png`. Les contrôles couvrent la structure visible,
l'absence de vérification active/IA implicite, le défilement autonome et
l'agrandissement du rapport, le JSON expurgé et la pagination 50 → 15.

**Limites explicites :** le probe monte le widget du plugin dans une fenêtre
GTK réelle, **pas** la fenêtre complète de Cifereca. Il n'atteste ni le parcours
de navigation depuis la bibliothèque, ni les clics physiques XTEST, ni le
presse-papiers système, ni l'accessibilité clavier complète. Ces contrôles
restent dans la recette D01–D16 de #1011 et nécessitent une seconde passe GTK.
Ne pas clôturer #1011 sur ce seul scénario.

L'environnement de ce changement ne fournit pas `gi` (PyGObject) :
**scénario créé mais pas exécuté**, aucune capture PASS annoncée.
Aucun nouveau workflow GitHub Actions.
