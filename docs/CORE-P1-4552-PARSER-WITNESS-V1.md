# P1 #4552 — diagnostic de régression par livre témoin

## Contrat local et déterministe

`notice_parse_witness.py` fournit un mécanisme sans réseau de comparaison
d'une **fixture approuvée** : SHA du corps déjà obtenu, portée source
exacte sous SHA opaque, version du parseur et chemins critiques structurés
des métadonnées parsées. Les chemins sont définis **explicitement** :
aucune recherche de texte, auteur ou ISBN approché n'est effectuée.

Le témoin précise, pour chaque champ, présence requise ou optionnelle et
type JSON, et éventuellement le digest d'une valeur **stable et
volontairement figée**. Par défaut on ne compare **pas** les valeurs
volatiles (résumé, prix, disponibilité, compte, liens signés). Ni réponse
HTTP brute, ni jeton, ni donnée personnelle ne sont persistés par ce module.

### Qualification du diagnostic

| Preuves | Résultat |
| --- | --- |
| Même scope, mêmes octets, parseur modifié, champ critique absent/altéré | `parser_regression_confirmed` |
| Mêmes octets et même version de parseur mais champ différent | `parser_drift_suspected` |
| Octets source différents, même notice | `source_changed` (jamais « bug certain ») |
| Témoin non approuvé, input SHA absent, profil/source différent, parsing partiel | `unverified` |
| Aucune différence critique sur même input | `unchanged` |

Les écarts sont référencés seulement comme `critical_field_N` dans la
sortie, sans afficher la valeur, le chemin, l'ISBN privé ou le scope. Le
résultat reste **non exécutable** : il ne remplace jamais une Notice source
ou consolidée saine, ne prouve ni droit ni permission, ne déclenche aucun
GET, réparation, rerun ou mutation.

## Conditions pour la future utilisation réelle

1. Le Core authentifie le témoin/sa baseline et son scope
   `source + edition + identité`, au besoin privée, et valide la
   cohérence de la version du parseur.
2. L'adaptateur parse hors réseau une **réponse déjà autorisée et présente
   en cache** (ou une fixture de CI), le Core calcule le SHA sur les
   **octets réels** fournis au parseur avant de comparer.
3. L'alerte de diagnostic est coalescée, versionnée et historisée dans
   le futur stockage Notice C. Un changement de réponse entraîne
   `source_changed`, pas `parser_regression_confirmed`.
4. Une confirmation de régression ne remplace ni un droit ni une publication
   valide ; l'utilisateur peut inspecter et revalider la baseline.

Limites : ce lot n'installe ni registre de témoins ni worker asynchrone, ni
notification UI, ni pipeline fournisseur. Les domaines d'observation et
versions de parseur doivent être validés par le Core à l'intégration
#2385/#3724 ; les comparaisons sur fichiers privés sont locales et
cloisonnées par identité.


## Destination des captures de régression (intégration Core Q7)

La clé de configuration locale `diagnostics.regression_capture_directory`
accepte `null` (défaut : répertoire Téléchargements) ou un chemin absolu,
éventuellement préfixé par `~` pour le dossier personnel. Exemple :

```json
{
  "diagnostics": {
    "regression_capture_directory": "/home/user/Diagnostics"
  }
}
```

Le Core lit cette valeur à l'ouverture, y compris sans admission du pump
optionnel. Une valeur invalide est refusée avant ouverture de SQLite, sans
repli implicite. Le dossier n'est créé qu'à l'export. Le même choix est
transmis au Search normal, au replay, au fetch autorisé et à la reprise
des intentions persistées. Il ne modifie ni l'activation du pump ni les
autorisations d'export des octets source.

Une modification de destination prend effet à la prochaine ouverture du Core ;
les archives déjà produites ne sont pas déplacées. La configuration ne
constitue pas une preuve que le dossier restera accessible au moment de
l'écriture. Une erreur d'export conserve l'intention réessayable ; une
archive partielle indique explicitement les membres omis. La présentation
GTK et le changement dynamique de destination ne font pas partie de cette
tranche.
