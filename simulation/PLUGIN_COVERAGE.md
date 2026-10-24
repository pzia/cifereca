# Matrice de couverture des plugins — inventaire P2 #770

Le rapport se reconstruit **depuis tous les manifests courants** de
`src/plugins/*/cifereca-plugin.json`. Aucun nombre de packages n'est figé.
La matrice reviewée `plugin_coverage.toml` conserve, séparément, les données
associées et les chemins de scénarios (contrat, erreurs/hors ligne, GTK).
Les chemins sont vérifiés dans le dépôt : une preuve absente, périmée,
extérieure ou un nouveau package non classé fait échouer le contrôle strict.

```bash
python3 simulation/plugin_coverage.py --strict
python3 simulation/plugin_coverage.py --json
python3 -m unittest discover -s simulation/tests -p 'test_plugin_coverage*.py' -v
```

### Colonnes et états

- `roles`, `plugin_ids`, `capabilities`, `ui_routes` : **lu dans le manifest**,
  et non deviné à partir d'une étiquette du tableau.
- `services` : correspondance du vrai identifiant plugin dans le catalogue des
  faux services ; `http_fixtures` : scénario HTTP injectable sans activation du
  fournisseur. Un service déclaré sans activation ne constitue pas un parcours.
- `data`, `fixture_profiles` : données locales/HTTP et profils configurés
  pour les produire ; l'existence d'un profil ne prouve pas que le plugin tourne.
- `unit_test_files` : nombre de fichiers de tests plugin, **pas de PASS CI**.
  `contract_tests`, `failure_tests` et `gtk_scenarios` : références
  explicitement auditées de fichiers existants. **Aucune exécution n'est
  inférée de leur existence**, ni des textes des noms de fichiers.
- `dimensions` : statut indépendant pour `http_fixture`, `dataset`,
  `contract`, `failure_offline`, `gtk_e2e`.
  - `partial` : au moins une pièce statique existe, mais la recette complète
    n'est **pas** attestée.
  - `absent` : aucune pièce recevable.
  - `not_applicable` : par exemple HTTP pour un plugin local qui n'est
    ni un provider HTTP ni un transport.
  - `covered` : réservé à une preuve d'exécution et de périmètre suffisants ;
    **jamais déduit du seul manifeste, des fixtures ou de scripts GTK**.
- `status` global reste `partial` tant qu'il existe seulement des preuves
  statiques. `e2e_gtk_verified=false` est volontaire : les scripts d'E2E
  ne sont pas une attestation de leur exécution.

### Limites et évolution

Ce rapport **n'exécute pas les suites** et ne lit aucune base personnelle :
aucun compte réel, secret, URL privée ni accès réseau. L'attestation d'exécution
GitHub Actions est un résultat distinct, qui n'est pas fabriqué par cet outil.
Certains plugins locaux n'exigent aucun serveur HTTP ; `ux-all` ne signifie
pas que tous les plugins ont une E2E GTK. Les états négatifs documentés peuvent
encore ne pas être exercés sur la GUI.

Un nouveau plugin doit avoir son bloc `[package.nom]` dans la TOML et des
preuves propres si elles existent. Ajouter un chemin de scénario seulement
s'il teste réellement le cas ; une référence périmée ou une preuve d'un autre
package est refusée. Les preuves de scénarios d'intégration communs vivent dans
`simulation/tests/`, les contrats internes dans `src/plugins/<package>/tests/`
et les probes GTK dans `e2e/`. Les tickets #771–#774 suivent les améliorations
du **contenu** des scénarios indépendamment de cet inventaire.

## JDD locaux ajoutés (pilote #772 et #773)

```sh
python3 simulation/plugin_scenarios.py --out /tmp/cifereca-ux-specialized
python3 -m unittest discover -s simulation/tests -p 'test_plugin_*.py'
```

Le générateur crée des données **fictives et déterministes** :
- `youth.json` : intérêt/lecture, 0–6, 17/18, âge inconnu/inféré,
  homonymes d'éditions et preuves ISBN contradictoires ;
- `criticism.json`, `criticism.rss.xml`, `criticism.jsonfeed.json` :
  0/1/N critiques, absence d'association, sources et ISBN ;
- `constellations.json` : titres reliés ou isolés, variations d'humeur,
  séries, accès et deux identités ;
- `optimizer-valid.epub`, `optimizer-corrupt.epub` : première paire
  succès/erreur technique.

Les fichiers sont produits dans `--out`, **pas versionnés**, et n'installent
aucun abonnement, réseau, identité utilisateur ou compte externe.

**État après l'intégration :** ces fixtures sont générées dans le runtime isolé
par `datasetctl` si `[specialized].enabled = true` (profils `ux-small`,
`ux-all`, `ux-errors`) et signalées par `summary --json`.
Les flux RSS et JSON Feed de critique sont désormais **servis par HTTP local** via
`simulation/editorial/pseudo_editorial_service.py` ; les JSON Jeunesse et
Constellations sont consultables comme fixtures, mais ne sont **pas des API
plugins injectées automatiquement**. Les fichiers ne sont pas injectés dans les caches métier,
ni recettées par le harnais E2E GTK. Elles ne simulent pas les
pages GUI OpenLibrary (le nouveau serveur HTTP simule leurs contrats),\nla découverte distante, l'ADEPT ni les formats
PDF/DRM. Lier ces scénarios à des faux services et des tests GUI réels reste
à faire dans les tickets dédiés.

## Éditions Milan — HTTP

Le service `milan` sur 127.0.0.1:8101 sert le catalogue et les fiches HTML
synthétiques ; `MilanYouthSource` est testé via `httpx.MockTransport`
injecté, sans aucun accès réseau extérieur. Les scénarios couvrent âges
attestés ou absents, ISBN valide/invalide, 0/1/N, pagination, 404, 503,
Content-Type invalide et dépassement de taille. Aucun plugin n'est
automatiquement activé ; ce n'est **pas** un test GUI GTK.

Voir `simulation/milan/README.md` et #816.

## Baen — couverture de contrat

`simulation/baen/pseudo_baen_service.py` sert maintenant les scénarios
catalogue, recherches, pagination, détails, téléchargements EPUB synthétiques,
429/503 et absence de lien. Le vrai `provider.baen` est exercé avec un
`httpx.MockTransport` dirigé uniquement vers le faux serveur loopback :
`simulation/tests/test_baen_http_service.py`. Le service n'active aucun
plugin Baen dans Cifereca et ne prouve pas un parcours GUI.

## Découverte de sources et enrichissement Jeunesse — scénarios exécutables

Le manifeste des JDD spécialisés passe à la **version 2** : les profils
`ux-small`, `ux-all`, `ux-errors` régénèrent automatiquement les
anciens caches lorsque la version change. Deux nouvelles fixtures synthétiques
sont accessibles via `CIFERECA_UX_SPECIALIZED_DATA` et le service éditorial :

- `youth-discovery.json` / `GET /youth/discovery.json` : résultats
  dupliqués, source attestant des âges, âge en mois, bruit documentaire,
  URL privée, credentials et inspection de page en erreur. Le vrai plugin
  `YouthSourceDiscoveryPlugin` construit un dossier avec revue manuelle.
- `youth-enrichment.json` / `GET /youth/enrichment.json` : preuve ISBN
  forte, éditions distinctes, conflit de doublons, titre seul refusé,
  séparation des identités, cache inaccessible. Le vrai
  `YouthDetailEnrichment` utilise un index **synthétique en mémoire**, sans
  recherche distante ni mutation du cache du Core.

`simulation/tests/test_youth_workflow_contracts.py` vérifie les deux plugins.
Les URLs `.example` restent fictives : **aucune requête HTTP externe** n'est
effectuée pendant l'exécution du workflow de découverte ou enrichissement.
La GUI GTK et la persistance applicative ne sont pas encore recettées.

## Kobo-Adept — profils synthétiques

`adept-profiles.json` fournit **cinq profils purement fictifs** (créé,
incomplet, prêt, sans liseuse et expiré) dans les JDD spécialisés.
`simulation/adept/fake_kobo_adept_cli.py` expose uniquement les commandes
de contrat et de profils, sans compte Adobe, jeton, certificat, ACSM ni DRM.
Le vrai `KoboAdeptRuntime` est testé via un sous-processus Python :
`simulation/tests/test_adept_profile_simulation.py`.

Un profil créé via la fausse CLI est volontairement **incomplet** : aucun
message de réussite de provisioning ne doit être inféré. Les opérations
d'association et de suppression sont des simulations sans mutation.
Il ne s'agit pas d'une validation GTK ni du véritable backend Kobo-Adept.

## Transport HTTP : erreurs et redirections déterministes

Le service `transport_failures` est accessible uniquement sur `127.0.0.1:8102` dans les profils `ux-small`, `ux-all`, `ux-errors`. Le vrai `HttpTransport` est testé contre 429/503, timeout, HTML ou MIME incorrect, taille excessive, réponse vide, boucle de redirection et redirection inter-origines supprimant les en-têtes sensibles. Un EPUB fictif permet aussi un téléchargement positif.

Ces tests ne constituent pas un scénario E2E GTK, une annulation de tâche ou une preuve de couverture du cache. Voir `simulation/transport/README.md` et `simulation/tests/test_http_negative_contracts.py`.

## OPDS Gallica/Gutenberg — erreurs de protocole

Le serveur OPDS existant accepte `?ux_fault=` pour sept états négatifs
de flux (429/503, XML invalide, mauvais document, zéro notice, échec de
page suivante, redirection bouclée). Des réponses EPUB invalides et
HTML testent `transport.http`. Les tests sont dans
`simulation/tests/test_opds_negative_contracts.py`.

Les scénarios sont **opt-in**, sans impact sur les catalogues des profils
`ux-small`, `ux-all`, `ux-errors` en mode normal ; les preuves de
fonctionnement GTK et les vrais fournisseurs ne sont pas vérifiés.
