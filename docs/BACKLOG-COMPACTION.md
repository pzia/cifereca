# Compactage Backlog — #8195 (tranche non destructive)

**État :** audit et validation structurelle seulement. Aucune autorisation de
destruction ; `apply` refuse systématiquement avant accès réseau.
Ne pas confondre un résultat structurel avec une équivalence sémantique,
un consentement ou une recette destructive.

## Exploitation

Backlog qualifie au plus trois issues closes (pas de PR), conserve les
invariants, exigences, décisions, exceptions, références et preuves uniques,
et prépare les synthèses dans un plan. CI/CD possède le script, ses tests et
le workflow. Les rôles propriétaires réalisent leurs PR documentaires ;
Backlog ne modifie pas les fichiers d'instructions et ne lance pas de CI.

Avec le checkout exact et un jeton **en lecture seule** dans GITHUB_TOKEN :

```sh
python3 scripts/backlog_compaction.py audit --issues 123,456
python3 -m unittest discover -s tests -p test_backlog_compaction.py
python3 scripts/backlog_compaction.py validate --pilot 999 --plan-sha256 <empreinte>
```

Le workflow manuel dédié expose le même audit/validation, jamais une mutation.
Les rapports contiennent les empreintes, ID et métriques, pas les textes.
La validation lit le corps JSON brut d'une issue de pilotage dédiée ouverte
(distincte de #8195 et des cibles), et compare SHA-256 des octets UTF-8 exacts.
Schéma v1 fermé (exemple fictif, SHA et dates à remplacer) :

```json
{
  "version": 1,
  "repository": "pzia/cifereca",
  "pilot_issue": 999,
  "main_sha": "<40 hex>",
  "baseline_sha": "8921002d1a5b9e0a4a0f0f592dbb6d669a283f4c",
  "created_at": "2026-11-27T12:00:00Z",
  "expires_at": "2026-11-27T13:00:00Z",
  "semantic_review": "Qualification humaine/Backlog des éléments préservés et références entrantes connues.",
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

Les snapshots incluent métadonnées et tous les commentaires paginés ; tout
écart détecté refuse le plan. Jusqu'à trois issues canoniques supplémentaires
peuvent être référencées dans retained_issues (lecture seule, ouvertes ou
closes, jamais PR) ; leurs empreintes sont vérifiées avant et après. Une
réécriture seule est admissible avec remove_comments vide si le corps change.
Les références explicites du corpus doivent rester présentes ; les liens
connus vers des commentaires à supprimer provoquent un refus. Cela ne prouve
ni équivalence sémantique ni absence de liens entrants inconnus. Revue Backlog
obligatoire. Baseline documentaire fixe : SHA initial du P0. Corpus comparable :
cibles + issues canoniques + neuf instructions + registre #3913 + ce runbook ;
le plan et tous ses commentaires existants sont comptés en surcoût. Tokens
estimés = octets UTF-8 / 4 arrondis au supérieur. Journal futur, autres frais de
pilotage et effets réels non comptés : aucun gain net réel attesté.

## Frontière de sécurité restant à établir

Audit du 27/11/2026 au SHA 8921002d1a5b9e0a4a0f0f592dbb6d669a283f4c :
main est signalée non protégée, rulesets vides ; accès à la protection
détaillée refusé (403), Environments non exposés par le connecteur.
Identité pzia et déclenchement workflow_dispatch seuls ne sont pas un
consentement indépendant. Il faut une barrière propriétaire indépendante de
la capacité d'écriture du connecteur, y compris modification du workflow
et déclenchement indirect. Aucune permission issues:write n'est attribuée
au workflow dans cette tranche.

Avant de rendre possible apply : établir la frontière effective et les
protections de main/workflow, le consentement spécifique lié à l'empreinte
et l'anti-rejeu persistant consommé avant toute mutation (incluant refus
des reruns), tests hors réseau et recette de test distinctement autorisée.
Sérialiser, vérifier avant chaque mutation, écrire et vérifier les synthèses
et les connaissances capitalisées sur main avant tout retrait. Un REST
non atomique conserve une fenêtre résiduelle ; arrêt sur échec partiel,
nouvelle autorisation pour reprendre, aucune destruction pendant un Focus
actif. Journal minimal, sans contenu supprimé. Aucun archivage systématique.
Le pilote de trois issues ne peut suivre qu'après ces preuves et un nouveau
consentement spécifique. Ne pas fermer #8195 sur cette tranche.
