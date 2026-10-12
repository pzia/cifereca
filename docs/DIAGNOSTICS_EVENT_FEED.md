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
est connue. Le champ facultatif `last_event` est l'horodatage UTC du **dernier
événement publié par cette source dans la fenêtre bornée du Core**, calculé
avant les filtres et la pagination. Il est absent si aucun événement publiable
n'a été trouvé ; il ne prouve ni l'exhaustivité de l'historique ni la fraîcheur
du fichier brut. Chaque événement expose `timestamp`, `source`, `module`,
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
applicatifs, `event_id` est une empreinte de la date, du niveau,
du module expurgé, du résumé publié et de l'identité interne du fichier
ouvert (périphérique/inode et position
absolue en octets). Elle reste stable lors du renommage standard
`cifereca.log` vers `cifereca.log.1` et lors du déplacement de la fenêtre
de lecture bornée, sans exposer ces métadonnées dans l'API. Des lignes
identiques demeurent distinctes. Pour les diagnostics structurés, deux
enregistrements avec la même date, catégorie et opération reçoivent
également des identifiants distincts dans le snapshot publié (désambiguïsation
par ordre d'apparition). La stabilité n'est garantie que tant que ces
enregistrements restent présents dans cette même fenêtre du Core ;
l'éviction des premières entrées peut renuméroter des doublons.
**Limite :** une rotation par copie,
restauration ou réécriture du fichier peut changer cette empreinte ; elle
n'est pas un identifiant global ou une preuve de corrélation métier.

**Couverture multi-source :** le champ global `status` devient `partial`
si une source demandée est partielle, ou si une source consultable coexiste
avec une source `disabled` ou `unavailable`. Il reste `unavailable`
si toutes les sources sont désactivées ou indisponibles ; il indique
`empty` seulement sans événement ni source manquante et `available`
lorsque des événements sont publiés sans lacune signalée. Les états de
`sources[]` restent la référence pour savoir quelle source manque.
Ce résumé ne garantit jamais l'exhaustivité historique.

**Limites connues :** le flux est un **snapshot borné**, pas une archive
historique globale. Lorsque le journal est tronqué ou rotaté, son état est
`partial`; on ne prétend pas retrouver tous les événements. L'explorateur
#1013 peut afficher ces entrées sans inventer des informations manquantes.
Un examen local manuel des journaux complets reste distinct et ne donne
aucune permission de lecture arbitraire à un plugin.

Priorité P0 ; l'analyse IA #1009 n'est jamais déclenchée depuis ce broker.
Aucune nouvelle GitHub Action.
