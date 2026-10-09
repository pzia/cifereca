# Instructions agents — Core

Hérite de [AGENTS.md](../../AGENTS.md).

Le propriétaire est **`core`** ; employer `coreui` comme alias lorsqu'une étape ne concerne que l'interface.

- **Core métier** : services, orchestration, données, stockage, configuration, API publique et framework de plugins.
- **CoreUI** : GTK/libadwaita, navigation, composants et contrats UI partagés.

À priorité égale, traiter le métier/Core avant l'UI ; **P0/P1/P2 priment toujours** sur cet ordre interne.

Ne pas déplacer dans le Core une logique spécifique à un plugin. Si le besoin appartient à un plugin, transmettre une issue à `plugins` ; pour une évolution d'API publique, documenter le contrat et coordonner avec ses consommateurs.

Profil : [.agents/roles/core.md](../../.agents/roles/core.md).
