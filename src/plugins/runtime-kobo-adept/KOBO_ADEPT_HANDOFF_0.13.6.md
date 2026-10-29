# Kobo-Adept — handoff 0.13.6

Version : **0.13.6**  
Contrat public : **v8**  
Download request : **schema v2**  
Client/orchestrateur courant : **cifereca**  
Contrat cifereca : **CIFERECA_KOBO_ADEPT_CONTRACT_1.3.md**

## 1. Statut

0.13.6 est une release d'alignement documentaire/distribution. Le moteur fonctionnel est inchangé par rapport à 0.13.5.
La surface publique reste celle de `KOBO_ADEPT_CONTRACT_0.13.4.md` / contract v8.

## 2. Architecture

```text
cifereca décide : identité, provider, candidat, Artifact, prêt, placement et UX.
Kobo-Adept exécute : HTTP, ACSM/ADEPT, loan_mgt, Kobo/KDRM et mutations device.
```

Acquisition et transfert device restent strictement séparés.

## 3. ADEPT / prêts

Surface d'automatisation consommée par cifereca :

```bash
kobo-adept loans list --profile NAME --json
kobo-adept loans expired --profile NAME --json
kobo-adept acsm return LOAN_ID --profile NAME --json
```

Le `loan_id` est distinct de tout identifiant provider. Lorsqu'il manque côté cifereca, la réconciliation automatique ne peut accepter qu'une correspondance unique entre `local_book` et le chemin canonique de l'Artifact.

Un retour réussi ne supprime ni l'Artifact local ni une copie device. Le nettoyage device reste une décision séparée via `cleanup`.

## 4. Device

Pour un prêt connu :

```bash
kobo-adept device import FILE --profile NAME --mount PATH --loan-id LOAN_ID --json
```

Le placement relatif reste pilotable par `--target-dir`; `target_relative_path` est la référence observée à conserver pour `presence` et `remove`.

## 5. Provisioning ADEPT

Workflow nominal cifereca :

```text
profile add NAME --username EMAIL --json
→ vérifier complete/fetch_capable
→ Kobo autorisée avec la même identité ADEPT
→ profile associate NAME --mount PATH --json
```

`adopt-kobo` / `import-kobo` restent des chemins alternatifs ou de diagnostic, pas le provisioning nominal cifereca.

## 6. Kobo/KDRM

`kobo_kdrm` passe par `source.type=kobo` avec recette delegated/pinned et contexte KDRM local.
Il ne doit jamais être aplati en HTTP direct.

## 7. Robustesse 0.13.5 conservée

Les diagnostics capturés de `acsmdownloader`, `adept_loan_mgt` et `adept_remove` sont décodés en UTF-8 avec remplacement des octets invalides. Un octet diagnostic non UTF-8 ne peut donc pas transformer une opération ADEPT en `internal_error` de décodage.

## 8. Contrats

```text
Contrat public Kobo-Adept : KOBO_ADEPT_CONTRACT_0.13.4.md
contract_version          : 8
download request schema   : 2
Contrat cifereca courant  : CIFERECA_KOBO_ADEPT_CONTRACT_1.3.md
```

## 9. Validation

Suite complète : **160 tests**.

## 10. Suite

```text
0.13.6  alignement contrat cifereca 1.3, aucune évolution de surface publique
0.14.0  lisibilité / DRM natif / résultat sémantique / fallback orchestrable
0.15.0  runtime libgourou géré
0.16.0  ADEPT/device approfondi
1.0.0   stabilisation contractuelle
```