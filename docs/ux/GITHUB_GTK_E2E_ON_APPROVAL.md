# Relais Debian 13 GTK temporaire — #1011 / #1143

## État réel (18 octobre 2026)

Le workflow actif `.github/workflows/gtk-e2e-on-approval.yml` **ne lance
aucun test GTK ni capture UX sur GitHub Actions**. Il prépare uniquement les
dépendances Debian 13 (trixie, amd64), à utiliser ensuite localement avec
`environnements/openai-chat-debian/bootstrap.py --offline`.

Le workflow ne possède **aucun** déclencheur `push` ou `pull_request`.
Il ne peut lancer les téléchargements réseau que par `workflow_dispatch`
après validation affirmative du booléen `confirm_relay` (désactivé par défaut).

## Autorisation et déclenchement

- L'opérateur choisit manuellement **Run workflow** dans GitHub Actions,
  sélectionne le commit de référence, puis coche `confirm_relay`.
- Ne jamais déclencher ce relais sans autorisation explicite.
- Les PR et modifications de la sentinelle historique
  `.github/gtk-e2e-run-request.txt` ne le déclenchent plus.
- Aucune exécution de ce relais n'est nécessaire pour vérifier ses tests
  d'allowlist (entièrement offline).
- La suspension budgétaire consignée le 11 octobre 2026 reste une raison
  supplémentaire de ne pas déclencher ce workflow sans nouvel accord.

## Contenu publiable

L'artefact `gtk4-debian13-offline-relay` (rétention : **2 jours**) ne
contient désormais que :

1. `gtk4-debian13-rootless-offline.tar.gz`, archive constituée exclusivement
   de paquets `.deb` du cache et du marqueur strict
   `.gtk4-cache-profile.json` (Debian/trixie/amd64).
2. `SHA256SUMS`, empreinte SHA-256 de cette seule archive.

`scripts/ci-validate-gtk-relay-cache.py` refuse les entrées inconnues,
répertoires imprévus, liens symboliques, noms ou en-têtes de paquets invalides,
profils inattendus et volumes excessifs. Rien d'autre dans le checkout ou le
runner n'est téléversé : **pas de source tarball, profile local, log, PNG,
screen.json ou packages-lock.json**. Les sources publiques sont accessibles
séparément par Git à leur SHA de commit.

Le téléchargement des dépendances utilise APT et ses dépôts Debian signés.
Le validateur assure la confidentialité du **contenu de l'artefact**, pas une
nouvelle vérification cryptographique des paquets : l'intégrité de la chaîne
APT reste une dépendance explicite.

## Recette locale et fermeture

Sur une machine autorisée : récupérer l'archive, contrôler `SHA256SUMS`,
extraire `.gtk-deb-cache/`, puis lancer le bootstrap rootless hors ligne et
le probe GTK local. Ne pas confondre **paquets disponibles** avec **E2E PASS**.

Ce relais est temporaire : le retirer après clôture de #1011. Le nettoyage
d'anciens artefacts et toute nouvelle exécution Actions exigent une décision
explicite. Les anciennes descriptions de téléversement de captures ou de
sources ne représentent plus le workflow actif.
