# #1005 — contrat public Release Update, phase 1 : lecture et prévalidation (v1)

Le plugin Dev Updater #794 ne doit **pas** importer `cifereca.updater`,
`cifereca.dev_updater` ni exécuter une commande système. Le runtime Core expose
désormais à la demande le broker optionnel
`context.broker("release.update.read")`, conforme au protocole public
`ReleaseUpdateReadBroker` (`cifereca.plugin_api`).

## API strictement en lecture seule

- `installation() -> ReleaseInstallationSnapshot` indique
  `installed/not_installed/unknown` et une **version syntaxiquement validée**,
  sans aucun chemin utilisateur, compte, inventaire plugin, identifiant
  d'installation ni contenu de manifeste. Une cible symlink en dehors du
  répertoire `releases/`, un manifeste absent/trop volumineux/invalide ou un
  lien vers un autre fichier ⇒ `unknown`. Aucune mutation ni réseau.
- `preview_release(announcement) -> ReleaseUpdatePreview` accepte seulement
  un aperçu borné de métadonnées **non fiables** d'une release GitHub officielle
  `pzia/cifereca`, publiée et stable, tag de version validé. `draft`,
  prerelease, source inconnue ou champs mal formés ⇒ code fermé
  `invalid_announcement/not_official_repository/not_published/channel_not_approved`.
  Une annonce valide demeure **`state=blocked`,
  `reason_code=provenance_not_verified`**. Aucune URL, hash fourni, sidecar ou
  booléen `verified` ne valide les octets.
- Types immuables `ReleaseInstallationSnapshot` et `ReleaseUpdatePreview`,
  exportés depuis `cifereca.plugin_api`, `schema_version=1`. Les champs
  `can_apply=False`, `provenance_verified=False`,
  `bytes_sha256_verified=False` restent faux dans **tous** les cas.

## Frontières de sécurité et canaux

- Le Core ne transmet **aucun** chemin, URL signée, token, version non validée,
  exception ou contenu brut du manifeste. La lecture concerne uniquement
  l'installation locale du Core ; la présence d'une version n'est **pas** une
  preuve de signature/provenance ni d'intégrité.
- `plan_release` applicable, `apply`, `download`, `restart`, gestion
  des credentials privés : **absents de ce broker**. La méthode nommée
  `preview_release` n'est **ni** un plan approuvé, **ni** un jeton de mutation.
- Aucun téléversement, scraping, déclencheur automatique, sous-processus, accès
  GTK, transaction, acquisition ou migration. Exposer ce broker n'autorise
  pas le plugin à exécuter une mise à jour.

## Phase suivante *non livrée* avant clôture #1005

1. Un plan Core véritable, portant sur un artefact téléchargé dans un contexte
   isolé, borné, associé à sa provenance vérifiée par une autorité de confiance
   **indépendante des métadonnées de la release** (attestation GitHub/Cosign)
   et son SHA-256 calculé **sur les octets exacts**.
2. Inventaire/compatibilité du Core et des plugins, locks de toutes les
   mutations critiques, refus de double déclenchement, deadline, annulation,
   sauvegardes et rollback Core+plugins, confirmés après redémarrage.
3. Progression typée expurgée, consentement explicite, credentials Core pour
   dépôt privé (jamais depuis le plugin). Validation manuelle + tests invalid
   signature, truncated asset, mismatch, lock/cancel/recovery.

Pour la livraison du plugin, utiliser la découverte optionnelle
`context.broker("release.update.read")`. Les actions d'installation restent
désactivées tant qu'un **autre** contrat Core d'application attestée n'existe
pas. Ne pas afficher « mise à jour sûre/applicable » à partir de
`ReleaseUpdatePreview` phase 1.
