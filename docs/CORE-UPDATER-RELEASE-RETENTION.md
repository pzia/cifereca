# Conservation des générations Core déployées — #4900

Contrat effectif pour `cifereca-update` et `cifereca-update-dev` (GitHub, ZIP/archive, source, snapshots dev).

## Politique cumulée

- Garder **toutes les générations installées depuis moins de 24 h** (fenêtre glissante, horaire UTC).
- Garder **au minimum les deux générations distinctes les plus récentes**, même quand les deux datent de plus de 24 h. Deux installations d'une même version avec des octets/commits différents constituent deux générations.
- Garder, même si cela porte le total à trois ou davantage, les cibles `current`, `previous`, et les références nécessaires à la restitution transactionnelle de `before_previous` lorsque le rollback rétablit les deux liens du Core.
- Une transaction incomplète ou en restauration **diffère toute purge**. L'analyse d'une référence absente/illisible ou d'un état inconnu échoue sans effacer.
- **Aucune purge de versions/plugins, backups utilisateur, SQLite, credentials, snapshots transactionnels ou données de profils**. Ne pas confondre les générations Core avec l'historique de packages plugins mentionné dans #3205.

La date d'installation est obtenue depuis `.cifereca-install.json:installed_at`. Les anciennes générations dépourvues de ce champ utilisent la date du dossier de release comme repli ; une métadonnée présente mais illisible/invalide est conservée par prudence. Une date future est conservée, jamais utilisée pour autoriser une suppression. Le timestamp des fichiers internes n'influence pas l'âge de la release.

### Invocation

`cifereca-update --github main` : **24 h + 2 générations** par défaut.

`cifereca-update --keep 4 --github main` : **24 h + 4 générations**. `--keep` reste un *plancher*, pas un plafond des générations récentes. `--keep 1` ne contourne pas le minimum de 2.

`cifereca-update-dev` applique la même règle lorsqu'un snapshot inclut une génération Core.

## Moment de nettoyage et récupération

`plugin_activation` et `transaction_finalize` doivent être terminés avant `cleanup`. Une exception de nettoyage est déclarée comme échec ; la restauration transactionnelle Core+plugins demeure obligatoire. Les opérations de suppression ne suivent jamais les symlinks de versions ni de journal. La collecte s'applique uniquement sous `install_root()/releases`, pas aux sauvegardes.

`cifereca-update --status --json` indique la politique, le nombre de générations retenues, et l'horodatage/compte de la dernière collecte, sans recalcul SHA supplémentaire pour la politique. Le ticket #3205 garde la responsabilité de rendre le statut entier compact et de réserver les vérifications approfondies à une commande explicite.

## Matrice de tests

1. N déploiements en moins de 24 h : tous présents ; passé 24 h, exactement les 2 plus récents si rien d'autre n'est protégé.
2. À 24 h strictes, le délai est expiré ; à 23 h 59 il reste valide.
3. Plusieurs releases avec la même version sémantique mais code distinct.
4. `current` et `previous` jamais effacés ; `before_previous` utile au rollback complet également.
5. Interruption/restauration, transaction incomplète, dates corrompues, symlinks, ancienne release sans `installed_at`.
6. Même comportement Core en GitHub/archive/source/dev et qualité de `--status`.

**Limite** : les références transactionnelles actuellement protégées peuvent amener à conserver trois générations même après expiration des 24 h ; ce n'est pas une violation de la règle « au moins deux ». Aucun changement de rollback explicite n'est introduit.
