# P0 #4858 — D3, livraison Kobo sélectionnée (un seul livre)

## Contrat

`cifereca-cli kobo-sync-edition --credential <id> --item <edition> --confirm`
ouvre **uniquement** le compte Kobo demandé et exécute une acquisition
ciblée sous le verrou Core de maintenance/acquisition. Sans `--confirm`,
la CLI refuse **avant le démarrage du Core**.

- Une connexion Kobo habilitée et un unique emprunteur lié au profil Core
  sont indispensables. Le fournisseur doit proposer un ABI de livraison
  canonique complet ; les anciens manifests 0.7 ne sont jamais utilisés.
- La politique `sync.source.acquisition-policy.v1` doit être ON pour le
  compte exact. Un ancien bouton global Kobo, une empreinte SHA, un titre,
  un droit v0.7 ou un résultat de recherche ne valent pas consentement.
- Le Core lit une fois la ressource A authentifiée du fournisseur, choisit
  l'**unique** droit dont l'`edition_id` correspond exactement à l'argument
  fourni, et transforme le snapshot en une branche partielle contenant
  **ce seul droit**. Zéro droit ou plusieurs droits pour la même édition
  → refus explicite, **aucun GET**.
- Le moteur existant `persist_attested_source_references` sauvegarde A
  (source attestée seulement), puis `_run_observed_canonical_convergence`
  utilise, sous **le même verrou**, les offres sélectionnées, les
  consentements révocables, la reprise et le publisher B atomique.
  Ni `backup_items`, ni `_execute_kobo_acquisition` ne décident du droit.
- Le retour est un code fermé et un ensemble de compteurs 0–1.
  Les identifiants personnels, chemins, liens signés et traces fournisseur
  ne sont jamais renvoyés. Une livraison seulement décrite ou un
  inventaire sans exécution ne devient pas un succès.

## Usage

```sh
cifereca-cli kobo-sync-edition \
  --credential mon-compte-kobo \
  --item edition-exacte \
  --confirm
```

Une édition avec plusieurs droits possibles (achat/prêt) doit être revue :
cette entrée **ne choisit pas arbitrairement** la branche à télécharger.

Cette nouvelle voie D3 est indépendante du vieux
`LibraryService.acquire_direct_item` et de `kobo-resume`. Leurs
compatibilités W2 et leurs lecteurs D4 continuent de nécessiter un cutover ;
ne pas fermer #4858/#3196 sur cette seule livraison.

Recette : `tests/test_core_p0_4858_d3_exact_kobo_selected.py` :
un seul droit dans la livraison, journal v0.7 inaccessible, erreur de
confirmation, emprunteur rebind, consentement retiré, source ambiguë,
absence de succès inventé, sortie CLI expurgée.


## Correctif D4 — compte Kobo connecté mais non enregistré en mémoire

Cas réel observé le 07/11/2026 sur `kobo-2` :
`cifereca-cli kobo-sync-edition --credential ... --item ... --confirm --trace`
obtient `vault connected` mais s'interrompt aussitôt avec
`selected_source_unavailable`, sans POST d'offre ni GET contenu.

**Cause :** `connect_kobo_preview` crée volontairement une session sans
peupler `LibraryService.accounts`. Le moteur canonique D3 cherche
obligatoirement une ligne `Account` active de ce credential et retourne
`selected_source_unavailable` quand la liste reste `[local]`.

**Correction :** la CLI D3 doit appeler `connect_kobo_selected_edition`,
et **non** `connect_kobo_preview`. Le Core réutilise l'unique connexion
preview, puis revalide dans SQLite les coordonnées exactes
`identity/kobo/credential/account` déjà enregistrées, l'unicité du compte,
la connexion du plugin et l'identité active. Seulement après validation, il
installe **en mémoire** l'objet `Account` appartenant à cette session,
sans nouvelle requête réseau, sans écriture des tables de connections ni
connexion d'autres providers. Si une preuve manque, échec fermé et
aucune promotion de compte. Le moteur D3 réévalue toujours consentement,
source activée, identité, offre et publication après cette étape.

Un succès de connexion `provider_connect` n'est **pas** un succès d'acquisition
ou de lecture du manifeste. Aucun résultat K3 n'est inféré de ce bug.

