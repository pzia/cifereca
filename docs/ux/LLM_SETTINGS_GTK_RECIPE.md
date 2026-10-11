# Recette GTK réelle — Paramètres → Intelligence artificielle (#1099)

**Statut au 11/10/2026 : outil dédié prêt, exécution réelle bloquée sur cet
hôte** par l'absence de PyGObject et le DNS des dépôts Debian 13. Ce document
n'est pas une attestation de réussite.

## Périmètre et sécurité

Cette recette doit être exécutée **localement, hors GitHub Actions/CI**, par
l'agent UX, avec une fenêtre Cifereca réelle à **1366 × 900**, GTK4/libadwaita,
X11/XTEST, une configuration XDG éphémère et aucun compte utilisateur.

Le script `environnements/openai-chat-debian/probe_llm_settings.py` :
- ouvre réellement `MainWindow`, va dans Paramètres puis sélectionne l'onglet IA ;
- vérifie l'état initial désactivé **sans requête réseau** ;
- configure un endpoint loopback contrôlé, saisit le modèle au clavier XTEST,
  effectue **un seul** test synthétique après clic et inspecte le POST JSON ;
- vérifie l'enregistrement atomique dans le `config.json` isolé ;
- confirme que changer d'endpoint révoque l'autorisation distante et qu'Annuler
  restitue la dernière configuration enregistrée ;
- produit des captures `00-disabled.png` à `05-reset.png` et un `report.json`
  incluant statut, checks, nombre d'appels synthétiques et empreintes des images.

**Aucun** secret, jeton, livre, compte ou journal réel n'est communiqué au
serveur de test. La requête loopback utilise exclusivement :
`Test de connexion technique. Réponds uniquement OK.`

## Exécution rootless Debian 13

Depuis la racine du dépôt `pzia/cifereca` :

```bash
cd environnements/openai-chat-debian
python3 bootstrap.py
./gtk-python check_import.py
./headless --screen 1366x900x24 -- ./gtk-python \
  probe_llm_settings.py --width 1366 --out evidence/llm-settings-1099
cat evidence/llm-settings-1099/report.json
```

En environnement sans DNS, **ne pas** réactiver de GitHub Action pour
contourner le problème. Utiliser uniquement un cache Debian 13 signé et
préparé hors CI, puis `python3 bootstrap.py --offline --deb-cache <chemin>`.
Conserver l'archive du cache et son manifeste SHA-256.

Le succès est caractérisé par `"status": "PASS"`, des captures réelles, et
`"synthetic_calls": 1` avec `"server_failures": 0`. Une capture seule
ne prouve ni le flux de sauvegarde ni la confidentialité du POST.

## Ce qui reste hors de cette recette automatique

- Navigation intégrale par lecteur d'écran/accessibilité et revue du focus
  par l'agent UX ; vérifier défilement horizontal et lisibilité à 1366 px.
- Après redémarrage réel de Cifereca, vérifier la prise en compte du modèle
  par un plugin consommant `context.service("llm")`.
- Test d'un backend OpenAI-compatible véritable (Ollama local, puis HTTPS
  distant **uniquement avec autorisation humaine explicite**), modèles absents,
  authentification refusée, délai et annulation.
- Diagnostics IA #1009 : consentement distinct à chaque analyse.
- Ne pas clôturer #1099 / #1011 / #1009 sur la seule existence du script.

## Diagnostic d'exécution du 11 octobre 2026

Hôte Debian GNU/Linux 13, Python 3.13, Xvfb disponible.
`/usr/bin/python3 -c "import gi"` → `ModuleNotFoundError`.
Bootstrap rootless à partir du kit Debian 13 utilisateur : échec
`Temporary failure resolving 'deb.debian.org'` et
`security.debian.org`. Aucun test GTK dédié prétendu réussi.
La CI générique de la PR #1141 était verte mais **ne testait pas cet onglet**.
