# P0 #2628 — installation Chromium multi-instance

Les commandes de réparation Playwright Python et Chromium sont **volontaires** et
distinctes. Le téléchargement du navigateur ne démarre qu'après consentement,
diagnostic de prérequis et contrôle du cache/espace disque.

## Verrou partagé des instances Cifereca

- Le `threading.Lock` Core continue d'exclure deux installations simultanées
  dans un même processus.
- Sur Linux, chaque installation consentie acquiert aussi un `flock`
  **non bloquant sur le descripteur en lecture du répertoire HOME validé**.
  Deux instances Cifereca partageant HOME ne téléchargent donc pas
  simultanément dans le même cache. Une instance concurrente reçoit le
  code fermé `installation_already_running`, sans lancer Playwright.
- **Aucun lockfile, cache artificiel ni mutation du répertoire HOME** :
  seul un descripteur POSIX est tenu jusqu'à la fin de l'installation et de
  la vérification. Fermeture/crash du processus libère le verrou OS.
- Sous verrou, le Core **revérifie la révision installée** avant de télécharger.
  Une autre instance ayant fini son téléchargement n'entraîne pas de second
  téléchargement. L'annulation entre le pré-contrôle et l'appel à Playwright
  est revérifiée juste avant la création du sous-processus.
- Ce verrou est une coordination **entre instances Cifereca coopérantes**,
  pas une protection contre des clients Playwright externes qui ignorent
  `flock`. Les révisions des autres clients sont conservées
  (`--no-remove`).
- Les erreurs de verrou restent fermées ; aucun chemin de HOME, cache, proxy,
  code d'exception, capture, identifiant ou nom d'utilisateur n'est publié.

## Recette hors réseau

`tests/test_core_p0_2628_cross_process_lock.py` lance un deuxième vrai
interpréteur Python qui détient le verrou sur un HOME jetable, puis prouve :
refus sans processus de téléchargement, absence de lockfile/cache, libération
après arrêt, annulation avant toute création de processus, et absence de
téléchargement redondant après installation concurrente. Le vrai
téléchargement de la révision Playwright associée reste testé séparément dans
les recettes Chromium Linux/GTK opt-in.

**Limites ouvertes :** traitement proxy/offline/CDN réel, filesystem
interdisant les verrous POSIX, et acceptation humaine sur Linux vierge.
