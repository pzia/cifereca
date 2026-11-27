# Compactage Backlog — #8195

Backlog qualifie et autorise le plan exact sous délégation propriétaire pzia
(arbitrage #8195 du 27/11/2026). CI/CD exécute et vérifie ; les rôles
propriétaires réalisent les PR documentaires. À la clôture de chaque P0,
Focus/Backlog applique la [politique commune](AGENT-DELIVERY-POLICY.md#consolidation-à-la-clôture-dun-p0).
Le Focus clôturant peut coordonner ; aucun autre agent ne doit écrire sur
le corpus pendant l'application. Backlog vérifie cette condition, les preuves uniques,
décisions, exigences, exceptions, liens entrants connus et le gain global.
La séparation des rôles est procédurale : même identité pzia, main non
protégée lors de l'audit, rulesets vides, protection détaillée inaccessible.
Ni l'identité ni le workflow ne prouvent une séparation cryptographique.

## Exploitation

Audit/validation en lecture seule avec GITHUB_TOKEN et checkout exact :

```sh
python3 scripts/backlog_compaction.py audit --issues 123,456
python3 -m unittest discover -s tests -p test_backlog_compaction.py
python3 scripts/backlog_compaction.py validate --pilot 999 --plan-sha256 <empreinte>
```

Le [workflow dédié](../.github/workflows/backlog-compaction.yml), exclusivement
workflow_dispatch sur main, offre ces deux opérations et apply. Ce dernier
nécessite premier run pzia, SHA exact, plan autorisé et revue Backlog.
Aucun autre workflow modifié ; aucun secret supplémentaire. Permissions
contents:read/issues:read pour audit, contents:read/issues:write pour apply.
Aucun rerun, même après échec. Pas d'appel apply depuis un poste local.
Le connecteur actuel n'expose pas workflow_dispatch : déclenchement via
l'interface GitHub avec les entrées exactes. Recette réelle de test à
réceptionner avant tout pilote historique (maximum trois issues closes).

Issue de pilotage dédiée ouverte, usage unique, distincte de #8195, des cibles
et des canoniques. Corps JSON brut, SHA-256 des octets UTF-8 exacts.
Schéma v2 fermé (exemple fictif, SHA et dates à remplacer) :

```json
{
  "version": 2,
  "repository": "pzia/cifereca",
  "pilot_issue": 999,
  "main_sha": "<40 hex>",
  "baseline_sha": "<même SHA que main_sha>",
  "closure_p0": 123,
  "created_at": "2026-11-27T12:00:00Z",
  "expires_at": "2026-11-27T13:00:00Z",
  "semantic_review": "Preuves et exigences transférées, références vérifiées.",
  "execution_review": {
    "corpus_quiescent": true,
    "known_inbound_references": true,
    "global_gain": true
  },
  "retained_issues": [{"number": 456, "snapshot_sha256": "<64 hex>"}],
  "issues": [{
    "number": 123,
    "snapshot_sha256": "<64 hex>",
    "body_after": "Synthèse proposée",
    "remove_comments": [456],
    "rationale": "Motif précis de chaque retrait"
  }]
}
```

`closure_p0` désigne le parent P0 déjà clos, cible ou canonique conservée.
`null` est réservé à la recette initiale et au pilote historique #8195.
Backlog atteste le lien de chaque cible avec ce parent et le gel du corpus.
Revue déclarative, pas une preuve automatique d'équivalence. Maximum
24 h, trois cibles closes et dix commentaires au total, trois canoniques
ouvertes ou closes en lecture seule, jamais PR. Corps seul possible si
modifié ; aucune suppression d'issue entière. Les empreintes couvrent
métadonnées et commentaires paginés ; références explicites du corpus
conservées, liens connus vers commentaires retirés interdits. Vérifier
aussi les liens hors corpus, capitalisations et leurs empreintes sur main.

## Application et réception

La consommation persistante est un commentaire minimal
BACKLOG-COMPACTION-CONSUMED v1 (empreinte + run) vérifié avant toute
mutation cible. Toute présence du marqueur interdit le rejeu de cette issue,
y compris après échec partiel. Ne jamais modifier, effacer ou réutiliser ce
contrôle ; il est éditable par pzia, pas un verrou inviolable. Concurrence
sérialisée par le workflow ; vérifier absence d'écritures concurrentes hors
workflow. Le REST n'est pas atomique : une fenêtre résiduelle subsiste entre
lecture et mutation. Vérification du corpus, expiration et main avant
chaque mutation, écriture et relecture de toutes les synthèses avant toute
suppression de commentaire. Aucun changement de métadonnée hors effet
serveur attendu. Tout écart/échec arrête ; inventorier l'état réellement
observé, nouveau plan et nouvelle autorisation Backlog pour reprise.
Aucun contenu retiré dans le journal ; pas d'archivage systématique.

Mesures caractères/octets/tokens estimés (octets UTF-8 / 4 arrondis au
supérieur). Corpus comparable : cibles, canoniques, neuf instructions,
registre #3913 et ce runbook ; baseline d'application égale au HEAD courant
pour ne jamais recompter un gain documentaire historique. Backlog mesure
séparément les changements documentaires entre SHA avant/après de la clôture,
une seule fois (baseline initiale #8195 : 8921002d1a5b9e0a4a0f0f592dbb6d669a283f4c).
Plan et commentaires de pilotage comptés en surcoût. Apply refuse un gain borné
non positif après marqueur et rapporte le gain borné réel après relecture.
Backlog doit ajouter le coût de #8195, PR et autres journaux et vérifier
l'équivalence sémantique : ni STRUCTURAL_VALIDATION_ONLY ni
APPLIED_BOUNDED_PLAN ne valent réception globale. Ne fermer #8195
qu'après recette de test, pilote réel et capitalisation effective.
