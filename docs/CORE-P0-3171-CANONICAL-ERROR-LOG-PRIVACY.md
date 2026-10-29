# Core P0 #3171 — journaux de Sync canonique expurgés

## Contexte et contrat

La publication canonique est déjà conditionnée au compte/credential exact, au
consentement persistant révocable, au verrou d'acquisition partagé et aux
preuves de livraison. Les exceptions provenant de fournisseurs, d'un état de
consentement ou d'un chemin ACSM peuvent inclure des identifiants privés, des
cookies, des jetons ou une URL signée. Les erreurs dans le pipeline Core ne
doivent jamais exporter ces payloads dans le journal brut ni les diagnostics.

La présente tranche n'ajoute **aucune autorité ni opération** : elle modifie
uniquement les journaux du pipeline Core `#3171`. Les décisions d'échec,
la conservation des données, l'idempotence et les gardes SQL/FS restent les
mêmes. La capture complète des exceptions est volontairement supprimée sur
ces chemins ; l'opération est observable par un **code fermé** stable.

| Phase | Code journalisé, sans identifiant ni traceback | Conséquence métier |
| --- | --- | --- |
| Admission d'un compte non lié en v14 | `canonical_rights_core_binding_changed` | aucune SourceReference ajoutée |
| Lecture initiale du consentement | `canonical_consent_read_failed` | consentement vide ; aucun GET |
| Revalidation du consentement | `canonical_consent_lookup_failed` | pending ou blocked ; aucun nouveau droit |
| Livraison ou commit en erreur | `canonical_delivery_or_commit_failed` | failed ; jamais de faux reçu |
| Marquage post-commit du reçu | `canonical_intake_state_update_failed` | OriginLink/Placement déjà publiés conservés |
| Projection de catalogue post-commit refusée | `canonical_catalog_index_failed` | aucune révocation et pas de nouveau GET |
| Projection de catalogue différée | `canonical_catalog_index_deferred` | reprise au scan normal |

## Réception synthétique

`tests/test_core_p0_3171_canonical_log_privacy.py` injecte une chaîne
privée dans les exceptions du lecteur de consentement, de la revalidation,
du downloader, de la projection descriptive et du marquage du reçu.
Les assertions contrôlent l'absence du secret et de traceback dans
`caplog`, le code fermé, les comptes de publications réelles dans SQLite
et l'absence de GET lorsque le consentement échoue. La publication déjà
committée résiste à un échec de projection ou de marquage ; une seconde Sync
ne répète pas le GET.

Les tests sont hors réseau et sans données de production. Ils **ne prouvent
pas** la recette fournisseur réelle de #3171, les migrations 0.7/0.8 de
#3196 ni l'isolation Ubuntu sur volume réel #3642. La politique de logs
des plugins demeure sous la responsabilité de B (#3252 / #979).
