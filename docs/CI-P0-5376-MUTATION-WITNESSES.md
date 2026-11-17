# P0 #5376 — témoins de mutation hors réseau

`scripts/ci_security_mutation_campaign.py` exécute une campagne volontairement
**bornée**, sans modification des sources de production : chaque mutant est
recompilé dans un processus pytest enfant à partir du HEAD testé. La baseline
des témoins doit être verte ; seuls les échecs d'assertion pytest (code 1,
résultat `FAILED`) prouvent que le mutant est tué. Un échec de collection,
d'import ou d'environnement n'est pas compté comme protection détectée.

| Site muté | Invariant observé | Témoins |
|---|---|---:|
| `public_httpx_transport._public_socket` — DNS privé accepté | Aucune socket privée après résolution | 1 |
| `public_httpx_transport._public_socket` — pair connecté non contrôlé | Rebinding DNS / pair réel refusé | 1 |
| `attested_https_delivery._admitted` — consentement ignoré | Refus avant second GET et avant publication | 2 |
| `attested_https_delivery.download_attested_https` — comparaison SHA finale neutralisée | Modification des octets en place, inode et taille inchangés, refusée | 1 |

**Quatre sites de mutation distincts, cinq témoins comportementaux.** Les
cinq sont des tests existants de la suite Core, avec DNS/socket
factices, HTTPX MockTransport et répertoires temporaires.

Commande dans un environnement de tests dédié :

```sh
python3 -m scripts.ci_security_mutation_campaign --json
```

Le verdict n'est pas une mesure globale de mutation ni une recette avec
fournisseur réel. L'identité de compte, le CAS métier et SQLite/recovery
nécessitent leurs propres témoins ; ce lot n'en revendique pas la couverture.


## Matrice de clôture propriétaire #6560 (17/11/2026)

La commande dédiée `python3 -m scripts.ci_security_mutation_campaign --closure --json`
exécute six sites, chacun avec un témoin ; la campagne historique ci-dessus
reste intacte. Les mutations vivent uniquement dans les processus pytest
enfants, y compris les méthodes de classe (pas de fichier de production modifié).
La baseline doit réellement passer au moins un test, pas simplement être skip.

| Invariant | Mutation | Témoin |
|---|---|---|
| (i) exactitude du périmètre | `read_loan_inventory` ignore le credential de session | `test_wrong_session_credential_never_starts_loan_read` |
| (ii) consentement/révocation | `_admitted` accepte une admission révoquée | `test_admission_revoked_after_redirect_denies_followup_before_get` |
| (iii) non-rejeu | `_ConfirmedStageAttempt.claim` autorise une seconde utilisation | `test_confirmed_headless_loan_stage_survives_navigation_once` |
| (iv) intégrité | SHA final non contrôlé | `test_same_inode_overwrite_before_link_never_publishes_stale_digest` |
| (v) writer legacy | `Storage.backup_put` ignore le fence SourceReference | `test_two_storage_instances_observe_committed_typed_source` |
| (vi) sélection CI | graphe ignore une dépendance transitive supprimée | `test_deleted_transitive_root_fixture_still_requires_plugin_replay` |

Workflow dédié exact HEAD, JSON en artifact et verdict CI ; un mutant survivant,
une baseline non exécutée ou une erreur de collection ne valent pas preuve.
Une relance sur le SHA définitif des cutovers reste requise si un contrat concerné
change. Aucune revendication de PASS avant résultat effectif de ce workflow.
