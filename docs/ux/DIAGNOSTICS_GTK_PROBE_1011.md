# Recette UX Diagnostics P0 — scénario GTK4 isolé

**Préparation de l'environnement :** [environnements/E2E-UX.md](../../environnements/E2E-UX.md).
Pour Debian 13 rootless, utiliser la commande `headless` + `gtk-python`
du guide (le runner général masque actuellement `PYTHONPATH` de GI).


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
GTK réelle, **pas** la fenêtre complète de Cifereca. Le code du probe utilise
désormais XTEST pour une activation clavier et interroge le presse-papiers GTK,
mais **ces contrôles n'ont pas été validés par une exécution E2E PASS**. Ils
ne prouvent ni le parcours Bibliothèque → Diagnostics, ni une couverture
complète d'accessibilité clavier. Les contrôles D01–D16 de #1011 restent
à recetter. Ne pas clôturer #1011 sur ce seul scénario.

L'environnement local initial ne fournissait pas `gi` (PyGObject).
Un runner GitHub a depuis validé l'installation GI/GTK4/Xvfb, **mais pas
le scénario fonctionnel** ; les tentatives ultérieures ont été arrêtées
pour budget GitHub Actions. Aucune capture PASS du parcours n'est annoncée.
Ne pas déclencher de nouveau workflow ; privilégier la recette locale documentée.
