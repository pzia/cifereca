# Compatibilité de déploiement des options de comptes plugins (#3731)

L'introduction de `AccountOptionField` dans l'API provider est **additive**
pour les plugins anciens. Les plugins Paris, Gallica et Kobo qui importent
cette nouvelle classe requièrent désormais **Plugin API >=1.4.3,<2**,
ce qui empêche leur installation sur un Core trop ancien.

- Core `PLUGIN_API_VERSION = 1.4.3`.
- Paris : package `1.2.37` ; Gallica : `1.2.24` ;
  Kobo : `1.2.26`. Les `plugins[].version` restent alignées.
- BnF-Prêt ne dépend pas de cette option : sa plage antérieure demeure valide.
- Pour les comptes Paris existants, l'ordre de priorité reste :
  `options.base_url` spécifique au compte, option fournisseur déjà enregistrée,
  clé globale historique `base_url`, puis défaut déclaré. Aucun écrasement
  ou effacement de valeurs privées.
- Les anciens descripteurs sans options restent acceptés par CoreUI. L'absence
  de champ de compte ne donne aucune permission de téléchargement ou Sync.

Aucun consentement ON n'est déduit de la migration, aucune donnée de bibliothèque
ni de droits ADEPT n'est modifiée.
