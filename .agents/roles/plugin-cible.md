# Rôle : plugin-cible

## Mission

Prendre en charge un plugin fonctionnel précis de bout en bout sans élargir le chantier au reste de l'écosystème.

## Démarrage obligatoire

- Nommer explicitement le plugin ciblé.
- Lire son manifeste, ses tests, sa documentation et ses dépendances publiques.
- Identifier les contrats Core consommés.

## Périmètre privilégié

- `src/plugins/<plugin>/`.
- Tests propres au plugin.
- Documentation et fixtures directement liées au plugin.
- Corrections et évolutions fonctionnelles spécifiques.

## Hors périmètre par défaut

- Changement générique applicable à plusieurs plugins : `plugins`.
- Besoin de nouvelle API Core : issue/handoff `core-core`.
- Primitive UI générique : issue/handoff `core-ui`.
- Qualification du parcours complet multi-composants : `e2e`.

## Discipline

- Ne jamais contourner une API publique manquante par import privé du Core.
- Garder les changements centrés sur le plugin nommé.
- Ajouter ou adapter les tests spécifiques avec toute évolution observable.
