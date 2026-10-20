# Diagnostic P0 #2380 — projection Kobo decision v1

Cette tranche porte **uniquement** la décision globale d'ouverture
`KOBO / automatic backup decision`. Le Core possède déjà deux observations :
l'événement `Diagnostics.add` et la trace machine
`TRACE kobo backup decision status=… enabled=… connected=… eligible=… interval=…`.
La projection publique utilise un **parseur fermé commun**. Elle ne copie
jamais un message texte fournisseur ni la totalité d'un diagnostic privé.

| Champ exposé | Condition de preuve | GUI / Diagnostics / export volontaire / log projeté |
| --- | --- | --- |
| `operation_kind=kobo_backup` et `phase=decision` | Opération Core exacte ou trace complète attendue | Oui |
| `status` | Valeur dans l'énumération fermée des décisions Kobo | Oui |
| `enabled` | Booléen exact, pas `0`/`1` ni texte libre | Oui |
| `connected`, `eligible` | Entiers 0–100 000, `eligible <= connected` | Oui |
| `interval_minutes` | Entier 1–100 000, borné | Oui |
| `task_ref` | Aucune preuve partagée pour cette décision | **Absent** |
| Identités/comptes, chemins, URL signées, tokens, exception, titres, droits | Informations privées ou non attestées | **Interdit** |

Un log DEBUG libre qui commence par le bon préfixe mais inclut un suffixe
inconnu n'est pas une trace valide. Une ligne absente (rotation, fenêtre de
32 Kio, DEBUG désactivé) ne prouve pas une absence de décision. Le niveau
INFO peut contenir cette trace, indépendamment de l'activation de DEBUG.
Le journal et Diagnostics publient le **même détail technique** lorsqu'ils
observent chacun cette décision ; leurs `event_id` restent indépendants.
**Aucune corrélation temporelle n'est fabriquée.**

Pas de nouveau collecteur, de transmission, d'appel fournisseur, de
téléchargement, de réparation ni d'activation de débogage. La diffusion
hors poste (rapport exporté, ticket support, CI ou LLM) requiert toujours
un acte explicite et le même filtrage. La possibilité qu'un plugin Python
hostile falsifie une émission en processus n'est pas une frontière de
sécurité du modèle de plugins. Tests synthétiques uniquement ; recette
sur journaux réels restant nécessaire.

Les autres producteurs Kobo, Numilog, Paris/PNB et ADEPT et la
référence commune des tâches relèvent toujours du P0 #2380.
