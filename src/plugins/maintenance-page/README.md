# Outils / Maintenance

Page autonome `ui.page.maintenance`, route stable `tools`, activée par
défaut. Ne dépend d'aucun outil fonctionnel. Le host générique fournit
des widgets et leur disposer via `UiMaintenancePageHost`. Le plugin
possède le conteneur, l'actualisation et le cycle de vie ; le Core conserve
l'admission, les confirmations, la file de tâches, les annulations,
la présentation générique et les invariants métier.

Le manifeste exige `maintenance.page.host` : le package reste bloqué
sur les hôtes antérieurs au cutover, sans remplacer prématurément la
route standard. L'enregistrement de cette capacité, le retrait de la
route native et les adaptateurs transitoires doivent être livrés ensemble.
Aucun accès Storage, aucun outil nommé en dur et aucun scan au montage.
