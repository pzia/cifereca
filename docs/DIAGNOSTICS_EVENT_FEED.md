# P0 #1010 — Flux public Diagnostics, lecture bornée

Le broker public `context.broker("diagnostics")` expose désormais :

```python
feed = broker.events(
    source="all",            # all, core_events, app_log
    module="",               # préfixe du module
    severity="",             # debug, info, warning, error, critical
    since=None, until=None,  # ISO-8601 avec fuseau obligatoire
    limit=50, offset=0,      # limit entre 1 et 100, offset entre 0 et 10000
)
```

La réponse est un dictionnaire `status / sources / events / total / next_offset /
read_only / bounded`. Chaque source expose un statut explicite : `available`,
`empty`, `disabled`, `unavailable`, `partial`, avec une raison lorsqu'elle
est connue. Chaque événement expose `timestamp`, `source`, `module`,
`severity`, `message` (résumé expurgé), `event_id` pseudonymisé.
Les valeurs `status` des Diagnostics structurés sont normalisées.

**Sécurité :** le chemin du journal est imposé par le Core
(`cifereca.log` + rotations `.1/.2/.3`), jamais par le plugin. Fichiers
réguliers sans lien symbolique, lecture des dernières 32 Kio maximum par
rotation, 200 événements maximum par fichier, 500 événements structurés
maximum en mémoire. Aucune lecture de base SQLite, credential, ACSM,
stdout/stderr brut, autre répertoire, réseau ou opération fournisseur.

Pour les journaux libres, la sortie **ne restitue pas le texte original** :
les seules données conservées sont le module, le niveau, le timestamp et une
signature opérationnelle `TRACE` à vocabulaire borné, éventuellement enrichie
de champs non secrets sur liste blanche (`phase`, `status`, `outcome`…).
Un message libre est remplacé par « contenu libre masqué ». Pour les
Diagnostics structurés, seuls les mots opérationnels autorisés sont diffusés,
pas les détails ni les valeurs potentiellement personnelles. Les empreintes
d'événements ne réintroduisent aucune valeur brute. Pour les journaux
applicatifs, les identifiants pseudonymisés s'appuient sur l'identité du fichier
(périphérique/inode), l'offset absolu de la ligne et les seules valeurs
opérationnelles publiques. Les deux premières informations ne sont jamais
exposées directement. **Une rotation par renommage** (`cifereca.log` →
`.1` → `.2`) préserve ainsi l'identifiant, même pour des lignes identiques.
Une rotation par *copytruncate*, une recopie ou la récréation du fichier
peut changer l'identifiant : aucun lien de provenance ne doit être déduit
dans ces situations. Le changement d'algorithme ne garantit pas la conservation
des anciens identifiants déjà exportés par des versions antérieures.

**Limites connues :** le flux est un **snapshot borné**, pas une archive
historique globale. Lorsque le journal est tronqué ou rotaté, son état est
`partial`; on ne prétend pas retrouver tous les événements. L'explorateur
#1013 peut afficher ces entrées sans inventer des informations manquantes.
Un examen local manuel des journaux complets reste distinct et ne donne
aucune permission de lecture arbitraire à un plugin.

Priorité P0 ; l'analyse IA #1009 n'est jamais déclenchée depuis ce broker.
Aucune nouvelle GitHub Action.
