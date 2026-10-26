# Instructions agents — Core

Hérite de [AGENTS.md](../../AGENTS.md).

Le propriétaire est **`core`** ; employer `coreui` comme alias lorsqu'une étape ne concerne que l'interface.

- **Core métier** : services, orchestration, données, stockage, configuration, API publique et framework de plugins.
- **CoreUI** : GTK/libadwaita, navigation, composants et contrats UI partagés.

À priorité égale, traiter le métier/Core avant l'UI ; **P0/P1/P2 priment toujours** sur cet ordre interne.

Ne pas déplacer dans le Core une logique spécifique à un plugin. Si le besoin appartient à un plugin, transmettre une issue à `plugins` ; pour une évolution d'API publique, documenter le contrat et coordonner avec ses consommateurs.

Profil : [.agents/roles/core.md](../../.agents/roles/core.md).

## Journalisation — règle impérative

Avant toute modification du Core, du logger, de l'API Diagnostics, de la CLI ou de CoreUI : appliquer **[le contrat canonique](../../docs/LOGGING-CONTRACT.md)** et **[le skill journalisation](../../.agents/skills/cifereca-logging/SKILL.md)**. Journal/console/broker/export brut complets, **aucune expurgation hors IHM** ; pseudonymiser **seulement login et mot de passe dans le rendu IHM**. Préserver le reste, y compris chemins, URL, statut HTTP et texte d'erreur. L'implémentation historique n'est pas réputée déjà conforme.
