# GTK4/PyGObject : récupérer les paquets Debian 13 via GitHub

**Objectif :** contourner le DNS sortant indisponible du conteneur UX sans
installer des paquets Ubuntu dans Debian 13.

## Principe

Le workflow `.github/workflows/gtk4-debian13-package-transport.yml` s'exécute
**uniquement sur demande** (`workflow_dispatch`), sur GitHub dans un conteneur
`debian:trixie`. Il télécharge avec APT les fichiers `.deb` PyGObject,
GTK4, libadwaita, Pillow, Xvfb et leurs dépendances, puis publie une archive
**GitHub Actions artifact** `gtk4-debian13-debs`, conservée **3 jours**.

Les paquets proviennent **des dépôts officiels Debian 13** ; GitHub assure
le *transport*, et **n'est pas l'éditeur** des binaires.

## Obtenir l'archive

Après fusion de cette PR sur la branche par défaut (GitHub n'affiche généralement
les workflows manuels qu'à partir de la branche par défaut) :

1. Dans `pzia/cifereca`, ouvrir **Actions → GTK4 Debian 13 package transport
   (manual) → Run workflow**. Ne lancer **qu'une seule fois**.
2. Ouvrir l'exécution réussie ; télécharger l'artefact
   `gtk4-debian13-debs` (ZIP GitHub).
3. Décompresser le ZIP dans un dossier : il contient
   `gtk4-debian13-debs.tar.gz` et son fichier SHA256.
4. Vérifier la somme SHA-256 de l'archive (le fichier accompagnateur vient de
   la même exécution GitHub ; vérifier également la provenance de l'exécution).
5. Installer sous **Debian 13 trixie**, pas sous Ubuntu :

```bash
sha256sum gtk4-debian13-debs.tar.gz
sudo bash environnements/install-gtk4-from-github-artifact.sh \
  ./gtk4-debian13-debs.tar.gz
/usr/bin/python3 e2e/run.py --python /usr/bin/python3 \
  --scenario diagnostics-1366
```

Le script d'installation refuse les OS autres que Debian 13, interdit
les chemins extérieurs au répertoire de l'archive, vérifie les sommes de
**chaque .deb**, installe avec `apt-get --no-download` et contrôle les
imports Gtk/Adw/PIL puis le démarrage de GDK sous Xvfb. Ne pas utiliser
`dpkg --force-depends`.

### Limites de sécurité et de reproductibilité

- Les sommes fournies dans l'artefact **ne remplacent pas** la vérification
  des signatures des index APT effectuée sur le runner GitHub. Ne faire
  confiance qu'à une exécution du workflow de ce dépôt, non modifiée.
- Les dépendances téléchargées sont résolues pour le conteneur Debian 13
  de GitHub ; une machine différente peut demander d'autres paquets, auquel
  cas `--no-download` doit échouer plutôt que procéder à une installation
  incomplète. Ne pas forcer les versions.
- Déclenchement **manuel**, aucune exécution à chaque push/PR ni nouvelle
  charge régulière sur GitHub Actions.
- Ce conteneur d'assistant **ne résout ni GitHub ni les miroirs APT** ;
  la connexion GitHub utilisée pour modifier le dépôt ne donne pas accès
  au DNS du shell. Le workflow n'a pas encore été déclenché ; aucun
  artefact n'est donc prétendu téléchargé ou installé.

Voir aussi `docs/ux/PYGOBJECT_DEBIAN13_E2E.md` et la recette #1011.
