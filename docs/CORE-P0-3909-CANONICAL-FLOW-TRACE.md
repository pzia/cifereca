# P0 #3909 — trace Core d'acquisition canonique corrélée

## Périmètre et preuve

La synchronisation Core possède déjà l'exécuteur unique A→B et les compteurs
terminaux `canonical_execution`. Une ligne `source_sync_partial` et le succès
d'un GET ne certifient pas la réception/publi­cation. Cette tranche ajoute des
événements **`TRACE canonical_flow`** sur le trajet *réel de l'exécuteur* :

- `observed/ok` : référence A traitée par Core, **pas** un GET ;
- `recovery/start|miss|ok` : tentative de récupération d'un reçu exact ;
- `delivery/start|ok|failed` : invocation du candidat et contrôle de son
  binding (un `ok` est **avant** le commit B) ;
- `receipt/start|ok|failed` : checkpoint durable permettant une reprise ;
- `promotion/start|ok|failed` : octets vérifiés dans le dossier final ;
- `publication/start|ok|failed` : transaction B/SQLite de
  `Artifact + Placement + OriginLink` ;
- `terminal/<status>` : verdict de cet item, raison fermée.
  Les statuts possibles sont `downloaded|recovered|present_attested|pending|
  blocked|failed|cancelled|skipped_archived|inactive`. Les événements
  `publication/ok` n'attestent **pas** l'index GUI ni l'entretien de doublons
  (tous deux post-commit).

Le format d'une ligne est :

```text
TRACE canonical_flow run_ref=<24 hex> item_ref=<24 hex> phase=<code-fermé> code=<code-fermé> reason=<code-fermé|none> kind=<purchase|loan>
```

**Confidentialité** : `run_ref` est aléatoire par invocation de l'exécuteur,
`item_ref` est un HMAC-SHA256 avec clé aléatoire **non journalisée** et
éphémère de la référence A exacte, tronqué à 96 bits. Un item conserve la
même référence entre phases **dans son run**, mais pas d'un run à l'autre.
Aucune valeur titre, ISBN, source, credential, compte, édition, right,
`offer_id`, URL signée, chemin, SHA, ACSM, exception ou texte HTML dans
ces messages. Les phases, verdicts et raisons sont allowlistés ;
une erreur du logger est sans conséquence sur le commit SQLite.

Les `canonical_*` agrégés de `source_sync` restent la vue terminale
recommandée. Les lignes corrélées permettent de qualifier une panne
`delivery` vs `receipt` vs `promotion` vs `publication`, sans fabriquer
de fichier local ni de droit. **Elles ne sont pas une preuve physique isolée** :
pour recette, croiser `acquisition-audit --verify-files`, Placement présent,
OriginLink exact et présence/empreinte du fichier final dans le même profil.

## Tests et limite de tranche

`tests/test_core_p0_3909_correlated_flow_trace.py` couvre un GET synthétique
→ reçu → promotion → publication, une seconde Sync sans GET, une panne
physique de `os.link` puis récupération du reçu et publication sans nouveau
GET, et une injection de champs privés refusée dans le journal.

Ce n'est **pas** une recette des six achats Numilog ni des prêts BnF/Kobo en
production. Aucun nouveau réseau ni nouvelle permission n'est autorisé par
le traceur. Les vieux reçus incomplets non liables à une source exacte ne
sont jamais adoptés à partir de `item_ref`, du titre ou de la SHA.
