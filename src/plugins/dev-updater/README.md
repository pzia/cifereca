# Dev Updater — reconnaissance des releases GitHub (P2 #794)

**Version 0.1.0 : consultation seule.** Le plugin est **désactivé par
défaut**. Il ne vérifie aucune mise à jour à l'ouverture de l'application
ou de la page : cliquer sur **Vérifier les releases GitHub** déclenche
une unique requête HTTPS, via le broker public `tasks` hors thread GTK.

## Paramètres

- Dépôt public : `pzia/cifereca` par défaut, `owner/repository` strict.
- Canal : `stable` (exclut les préversions), `prerelease` (seulement
  les préversions), ou `dev` (toutes les releases publiées).
- Aucune authentification n'est acceptée par ce stade du plugin.
  Un dépôt privé / token exige un contrat public Core sécurisé.
- L'API GitHub consultée est uniquement
  `https://api.github.com/repos/<owner>/<repository>/releases?per_page=30`.
  Pas de redirection, données JSON bornées à 512 Kio, délai de 10 s.
  Une release `draft` n'est jamais considérée comme publiée.

## Résultats et prudence

- Indique le tag, la date de publication, les notes bornées et
  les **noms** des ZIP et fichiers SHA/Sigstore déclarés.
- Compare les versions uniquement si elles sont comparables
  par `packaging.version.Version`, sinon l'incertitude est affichée.
- Une annonce `*.sha256` ou `*.sigstore.json` **ne constitue
  aucune preuve cryptographique vérifiée** : les octets ne sont jamais
  téléchargés et aucune installation n'est proposée.
- Ne modifie ni installation, ni fichiers de bibliothèque, ni
  plugins, ni inventaire local. Aucun redémarrage ni notification
  automatique n'est déclenché.

## Pour livrer la suite du ticket

Le rôle Core doit exposer un contrat public de *planification*
et d'*application* d'archives officielles : provenance/attestation,
empreinte cryptographique, ciblage des packages, transaction,
garde d'activité critique, rollback et confirmation post-redémarrage.
L'interface plugin devra consommer ce contrat, sans importer
`cifereca.updater`, `cifereca.dev_updater`, ni appeler un
sous-processus arbitraire. Le jeton GitHub privé nécessite une
gestion de credentials adaptée et négociée (pas de champ libre).
Une vérification automatique ne pourra être ajoutée qu'avec
une planification approuvée et désactivable, en conservant le
comportement sans réseau de la présente version.

Tests locaux :

```bash
cd src/plugins/dev-updater
python -m pytest -q
```
