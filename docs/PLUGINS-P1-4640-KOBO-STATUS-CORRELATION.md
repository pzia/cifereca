## Diagnostic privé des URL candidates (P1 #4882, opt-in Trace)

Lorsque **et seulement lorsque** le niveau de diagnostic effectif est `trace`,
le provider Kobo écrit dans le **journal local** de Cifereca.

Pour une vérification **K1 exactement ciblée**, la CLI démarre normalement
au niveau `detailed` ; le mode GUI ne se propage pas à un autre processus.
La nouvelle option `--trace` l'active explicitement pour cette invocation
confirmée (le niveau est restauré à la sortie) :

```sh
cifereca-cli kobo-sync-edition --credential '<id-compte>' \\
  --item '<id-édition-K1>' --confirm --trace
```

Pour **K3**, activer `trace` dans l'application puis lancer une
synchronisation Kobo déjà consentie, ou utiliser la capture ciblée
`cifereca-cli sync run --provider kobo --identity ID --credential ID
--confirm --trace-export ... --confirm-trace-export`
(ce ZIP contient **toute** la trace HTTP privée, distincte du simple journal).
La qualification K3 demeure un POST descriptif, **sans téléchargement**.

Format attendu du journal :


```text
TRACE kobo private_candidate_url phase=offer product_id='...' candidate_index=0 handling=kobo_kdrm url='https://...?...'
TRACE kobo private_candidate_url phase=offer product_id='...' candidate_index=1 handling=kobo_kdrm url='https://...?...'
TRACE kobo private_candidate_url phase=initial product_id='...' url='https://...?...'
TRACE kobo private_candidate_url phase=redirect product_id='...' url='https://...?...'
```

- `offer` : **toutes** les URL candidates du descripteur POST déjà reçu,
  dont les deux K3, même si l'offre reste ambiguë/pending. Aucun tri arbitraire
  et aucun GET supplémentaire.
- `initial` : URL sélectionnée pour la livraison Core, avant tout GET,
  y compris une URL K1 refusée en préflight.
- `redirect` : cible du saut HTTPS calculée avant son éventuel GET,
  y compris une cible rejetée par le validateur. Les contrôles SSRF/TLS,
  le consentement, les refus et les publications ne changent pas.
- Modes `normal` et `verbose` : **aucun de ces événements, aucun lien signé**.
  La frontière Core n'appelle même pas l'observateur URL en dehors de Trace.
- Les URL sont écrites en `repr` Python : une valeur contenant un saut de
  ligne reste sur **une ligne de journal**. Il ne faut pas supprimer les quotes
  ni décoder automatiquement les échappements pour faire des requêtes.

**Précaution explicite :** les URL et les paramètres de signature sont
potentiellement des secrets d'accès. Le journal `trace`, ses rotations et
les ZIP bruts exportés peuvent conserver ces données après avoir quitté
ce mode. Ne jamais les publier sur GitHub, une CI ou un ticket public ; ne
transmettre aux agents que les champs fermés (phase, catégorie, issue) ou
un extrait volontairement expurgé. Après diagnostic, remettre le mode normal
et purger **manuellement** les copies privées selon la politique locale.
Les jetons OAuth du compte ne sont **jamais** journalisés par cette fonctionnalité.

Cette observation n'est ni une permission de téléchargement ni une preuve que
deux liens K3 transportent le même fichier ; le refus `pending` reste maintenu.

# P1 #4640 — Kobo K1/K2/K3 : corrélation du status existant (Cifereca uniquement)

> **Mise à jour opposable — 07/11/2026, décision propriétaire [#4640](https://github.com/pzia/cifereca/issues/4640)** : les interdictions historiques de normaliser `unknown/flepub` et de préférer EPUB à PDF ci-dessous ne s'appliquent plus. K1 choisit l'unique EPUB **déjà admissible** parmi des offres plain EPUB/PDF ; deux EPUB restent ambigus. K2/K3 : Cifereca seul normalise **uniquement** `media_format=unknown` + `source_format=flepub` (casse/espaces tolérés) pour `http/kobo_kdrm/library_sync` et DRM KDRM reconnu ; le descripteur KVault reste brut. L'ID d'offre, le sélecteur exact, la recette runtime et le diagnostic utilisent la même normalisation ; K3 à deux candidats demeure `pending`. `candidate_shape_counts` conserve les formats bruts, `candidate_kdrm_flepub_normalized_count` indique uniquement le nombre normalisé, et `candidate_normalized_media_format=epub` est une catégorie fermée. Ni autorisation d'acquisition, ni preuve d'octets, ni téléchargement réseau supplémentaire. Le texte restant documente la phase d'investigation antérieure à la décision.


## Problème et portée

Les trois offres réelles sont encore `pending` sur la dernière preuve terrain :
K1 deux candidats admissibles et ambiguës ; K2 un `library_sync/KDRM`
`unknown/flepub` ; K3 deux paires KDRM rejetées. Les observations de
06/11/2026 précèdent l'instrumentation de #4728/#4729/#4733.

**Aucun changement KVault n'est requis ni autorisé pour #4640.** Le client
Cifereca a **déjà** interrogé `GET /api/v2/status` à `connect()` et
mémorise `self.vault_release` et `self.api_version`. Les valeurs sont
filtrées par `_vault_public_version` : un label malformé devient vide.

## Événement produit sans réseau supplémentaire

Quand le préflight exact termine en
`reason=descriptor_candidate_unqualified`, le même
`EVENT kobo selected_offer` inclut désormais :

- `kvault_status_release_at_connect=<version|unavailable>`
- `kvault_status_api_at_connect=<version|unavailable>`

Les deux champs sont ajoutés aux diagnostics **K1, K2 et K3** sans appel HTTP
ou nouvelle divulgation de payload. La version provient de la connexion
**antérieure**, pas du POST du descripteur. Elle **ne démontre pas** quelle
version du Worker a effectivement servi un POST ultérieur : une bascule de
déploiement, une route différente ou l'absence de `release` restent possibles.

Ne pas assimiler ce champ à `producer_contract` ni à un identifiant de
build. Le refus `unknown/flepub`, la sélection prudente de K1 et les profils
de rejet de K3 restent inchangés.

## Vérification ciblée à la prochaine observation autorisée

- **K1** : `candidate_route_comparison`, `candidate_shape_counts`, version
  de status au connect. Ne sélectionner aucune route par ordre ou similarité.
- **K2** : `candidate_kvault_flepub_contract`,
  `candidate_kvault_format_alias`, version de status au connect, puis
  vérifier **sans supposition** l'origine de `media_format=unknown`. Le
  contrat KVault de référence `epub/FLEPUB` ne justifie pas de coercition.
- **K3** : `candidate_rejection_profile_counts`,
  `candidate_shape_counts`, version de status au connect. Aucun GET.
- Une valeur `unavailable` signifie seulement qu'aucune version publique
  syntaxiquement sûre n'a été mémorisée ; pas preuve d'un vieux Worker.

Ne pas redemander le ZIP 06/11 déjà interprété. Pas de synchronisation
massive, pas de nouveau GET, pas d'évolution ni déploiement KVault. Les tests
synthétiques de la tranche couvrent les trois pending, les versions
malformées et l'absence de secret dans le journal.


## K3 : deux URL de même route, identité de fichier non démontrée

Le diagnostic des offres Kobo ajoute, **sans second POST ni GET**, la
relation structurelle `candidate_url_relation` aux catégories existantes
`eligible_candidate_count` et `candidate_route_comparison` :

- `same_host_path_different_query_or_fragment` : même hôte/chemin,
  query ou fragment distinct (signatures possibles, **équivalence non prouvée**) ;
- `same_host_different_paths` : même hôte, chemins différents ;
- `different_hosts` : hôtes différents ;
- `same_host_path_raw_encoding_difference` ou `uncomparable`.

Si plusieurs candidats demeurent admissibles, une observation locale contient
également leur **titre associé au même compte authentifié** :

```text
EVENT kobo ambiguous_candidates product_id=... title='...' candidate_count=2 outcome=pending non_failure=true
```

Aucune URL signée n'est incluse dans cette observation ; pour voir les URL
complètes utiliser **uniquement** le mode Trace privé livré dans #4890 :

```sh
cifereca-cli kobo-sync-edition --credential '<compte>' \\
  --item 'f4b22e3d-2980-4d33-bf0c-df75bfb1a24c' --confirm --trace
```

Cette commande cible K3, affiche son titre dans le journal privé et expose
les deux candidats (indices `0` et `1`) sans arbitrage arbitraire. Si
aucune offre exacte n'est sélectionnable, aucun contenu n'est téléchargé.
Le titre **n'est pas connu depuis le journal antérieur à cette instrumentation**.
Ne pas ajouter un fallback « premier candidat » et ne pas conclure à une
même édition binaire sur la seule comparaison d'URL.


## K3, 07/11/2026 : discriminer deux jetons de téléchargement

Observation terrain ciblée : le tome 1 de « Trolls de Troy – Histoires
Trolles » est `pending`, avec deux offres `library_sync:unknown:flepub:kobo_kdrm`
sur `storedownloads.kobo.com/download`. Le seul paramètre variable est
`downloadToken` ; **il s'agit de deux jetons de capacité opaques**, pas
d'une preuve d'EPUB + PDF ni de fichiers binaires identiques.

KVault `0.9.6.4` est la version effectivement déployée lors de cette
observation. La version `0.9.8` du dépôt `pzia/KVault` normalise
`FLEPUB → epub` et réduit les doublons `library_sync` par combinaison
`handling / DRM family / media_format / source_format / Platform`.
Cette politique ne garantit **pas** un candidat unique lorsque `Platform`
diffère. En particulier, ne jamais dédupliquer deux URL signées sur le seul
host/path : un jeton opaque peut désigner une variante différente.

Pour observer la dimension manquante sans nouveau GET :

```sh
cifereca-cli kobo-sync-edition --credential 'kobo-2' \\
  --item 'f4b22e3d-2980-4d33-bf0c-df75bfb1a24c' --confirm --trace
grep -F "TRACE kobo private_candidate_url phase=offer" \\
  "${XDG_STATE_HOME:-$HOME/.local/state}/cifereca/cifereca.log"
```

Chaque ligne Trace énonce désormais `platform=`, `profile=`, `source=`,
`media=`, `source_format=`, `drm=` pour l'index `0` ou `1`, en
plus de son URL privée déjà instrumentée. Les catégories sont **fermées**
et ne peuvent inclure le texte brut non reconnu du descripteur.
Pour partager le résultat, supprimer les valeurs `url=...` et tout
`downloadToken`. Aucun autre log ne reçoit ces nouvelles métadonnées,
et le comportement des critères Core A/B reste identique.

**Interprétation** : deux plateformes différentes appellent une décision
explicite fondée sur le profil du lecteur ; deux candidats de même
plateforme nécessitent de comprendre la duplication fournisseur (puis
éventuellement corriger KVault). Ne pas choisir le premier candidat
dans Cifereca avant vérification.
