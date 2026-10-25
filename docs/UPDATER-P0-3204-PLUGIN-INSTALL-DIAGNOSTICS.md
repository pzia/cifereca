# P0 #3204 — Diagnostic d'installation des plugins lors de `--github main`

## Contrat implémenté (première tranche)

La sortie du 25 octobre 2026 établit un timeout de 120 s **au premier des 21 plugins**, pas la cause du blocage. Après cette tranche, l'installateur expose, sous le même flux de progression déjà présent, les sous-phases fermées :

- `plugin_inspect` : lecture/validation de l'archive ;
- `plugin_unpack` : remplacement du contenu extrait ;
- `plugin_dependencies` : vérification/résolution/install de dépendances Python (inclut actuellement les opérations pip) ;
- `plugin_enable` : activation des contributions.

L'événement `plugin_install` conserve `plugin_index` / `plugin_total` **comme ordinal tenté**, et ajoute `package_id` et `package_version` issus du manifeste validé du snapshot, avec grammaire/longueur bornées. Ces deux valeurs ne sont **pas** acceptées depuis le stderr du CLI fils. Le stdout/stderr brut, les arguments, les chemins personnels, les identifiants de comptes et les messages d'exception ne sont jamais projetés depuis le processus fils dans la progression du parent.

Le CLI n'active ce protocole JSONL que si l'updater transmet la capacité interne `CIFERECA_UPDATE_PROGRESS_JSONL=1`. Les appels usuels à `cifereca-cli plugin install` conservent leur stdout JSON et leur fonctionnement normal. Le process parent utilise le collecteur commun `run_plugin_child`, qui draine les deux pipes avec limites, impose une échéance et tue le groupe de processus POSIX (y compris descendants héritiers) en cas de timeout/annulation, avant de rendre la main au rollback.

## Politique de délais

- **300 s par plugin**, deadline absolue, même si des heartbeats remontent. Une installation active pendant plus de 120 s n'est donc plus considérée comme expirée à ce seuil historique. `RUNNING` n'est pas une preuve que pip progresse, seulement que la sous-phase n'a pas encore terminé.
- **3 600 s maximum** pour le sous-processus du snapshot complet de release, contenant jusqu'à 21 packages. Ce plafond est indépendant et ne se renouvelle pas au changement de plugin.
- Aucun retry, processus d'installation parallèle, extraction implicite ou téléchargement supplémentaire introduit.

## Limites / prochain diagnostic

Le correctif distingue l'étape exacte du futur blocage, **pas** la cause interne de pip, du verrou ou de la lenteur de l'activation. Aucun compte fournisseur ni exécution réelle de `--github main` n'a été utilisée pour qualifier l'incident initial. Les étapes restantes dans #3204 sont : approfondissement du suivi pip/lock si la phase problématique le confirme, vérification post-rollback **en lecture seule**, et recette sur les 21 packages. Ne pas fermer #3204 sur cette seule tranche.

## Vérification

Tests synthétiques hors réseau : streaming de phases sous un compte de progression existant, référence de package issue du parent, rejet de valeurs privées injectées depuis le fils, installation avec progression qui excède un petit budget simulé, timeout terminant aussi le descendant avant rollback. La CI complète et le gate de mise à jour doivent être consultés sur le HEAD final de la PR ; aucun test local n'est annoncé comme exécuté.
