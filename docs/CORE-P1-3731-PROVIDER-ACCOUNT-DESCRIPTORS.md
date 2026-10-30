# Paramètres de comptes pilotés par les plugins — #3731

Le formulaire CoreUI **ne choisit plus des champs selon l'identifiant du fournisseur**.
Les descripteurs `ProviderDescriptor` exposent, de manière additive et
compatible avec les anciens constructeurs :

- `account_option_fields` : suite de `ProviderAccountOptionField(key, label,
  default, legacy_config_key)`. L'éditeur affiche une ligne texte par option ;
  la validation n'accepte que les clés déclarées et préserve les autres options
  déjà enregistrées dans `original_options`.
- `account_help` : textes de présentation propres au fournisseur ; jamais
  utilisés pour autoriser ou refuser une acquisition.
- `max_accounts_per_identity` et `account_limit_message` : limite par profil
  et fournisseur, sans référence explicite à Kobo dans le CoreUI.

Les plugins Paris, Gallica et Kobo ont été migrés. Le schéma est également
testé avec un fournisseur fictif inconnu. Les limites héritées de Kobo sont
conservées, mais leur politique est maintenant déclarée par le plugin.

**Compatibilité :** aucun compte, identifiant, secret, option inconnue ou
paramètre de téléchargement existant n'est effacé lors d'un enregistrement.
Un champ optionnel absent des anciens descripteurs n'est pas interprété comme
une autorisation ou une révocation. Pour Paris, `base_url` explicite prévaut
sur les valeurs héritées (`provider_option`, puis configuration globale),
puis sur l'URL officielle par défaut. Les versions des trois packages sont
incrémentées pour permettre au gestionnaire de mise à jour d'installer les
descripteurs enrichis.

Ce lot n'active aucune synchronisation : consentements source ON/OFF, scopes,
acquisitions et ADEPT demeurent sous l'autorité de leurs contrats Core.
Les anciens contrôles de backup Kobo dans Paramètres/Sync doivent être retirés
séparément, après bascule des parcours d'exécution vers le contrat canonique
#3719/#3416. Ne pas supprimer leurs données persistées lors de cette étape.
