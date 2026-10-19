# #2370 — diagnostics Core des échecs de connexion (contrat local v1)

Le Core connaît le **fournisseur** d'une tentative de connexion, mais jamais
l'origine d'une erreur par simple proximité temporelle avec un autre événement.
`plugin.ready`, `session.connected` et `catalog.search live` sont trois états
distincts. Les traces passées sans slug ne permettent pas de nommer les deux
sources en échec observées chez l'utilisateur.

## Projection publique, distincte des identifiants de connexion

`connect_configured()` conserve ses résultats privés existants
(`errors`, `connected` et comptes). Il ajoute **`connection_failures`** :

```json
{
  "items": [
    {"provider_id": "provider.test", "code": "provider_connect_failed"},
    {"provider_id": "source_inconnue", "code": "provider_connect_failed"}
  ],
  "truncated": 0
}
```

- Une entrée par tentative ayant échoué (y compris plusieurs comptes du même
  provider), **aucun identifiant de compte ni label libre**. Le seuil public
  est de **40 échecs**, le compteur `truncated` explicite le reste.
- L'identifiant est publié seulement si un **provider effectivement enregistré**
  dans le registre Core porte **exactement** le même identifiant canonique ASCII
  borné. Les IDs inconnus, non enregistrés, malformés ou ambigus deviennent
  `source_inconnue` ; aucune reconstruction depuis la clé privée des erreurs.
- `provider_diagnostic_state()` expose la **même projection sûre** pour
  Diagnostics/rapport support, en conservant `errors.connect_failed`
  numérique. Une reconnexion efface l'état courant avant de le recalculer.
- `Diagnostics.add_core_provider_connect_failure` est un producteur **interne**
  dédié : code `provider_connect_failed`, statut erreur, slug prévalidé,
  durée monotone entière optionnelle et bornée. L'opération est `provider
  connect` ou `public provider connect` (singleton). Un simple
  `Diagnostics.add(detail={"provider_id": ...})` fourni par un plugin
  **n'atteste pas** la source. `core_events` publie uniquement les événements
  marqués par le producteur Core et avec slug revalidé.
- L'ancien texte de log/diagnostics ne contenait pas l'attribution et ne
  peut pas être enrichi a posteriori. Les nouveaux logs Core incluent le slug
  **canonique** et un code fermé, jamais l'exception ou la configuration.

## Matrice de publication (#2380 — seule tranche connexion)

| Donnée | Retour Core / UI locale | Events / export support | Log applicatif | Artefact CI / IA |
| --- | --- | --- | --- | --- |
| slug de provider **du registre Core** | Oui (validé) | Oui (événement Core attesté) | Oui (slug validé) | Uniquement si export volontaire et même vérification |
| code d'échec **fermé** | Oui | Oui | Oui | Oui, sur export volontaire |
| durée monotone bornée | Optionnelle | Optionnelle | Durée observée | Sur export volontaire |
| ID de credential / identité, labels libres, comptes | Interdit dans cette projection | Interdit | Interdit | Interdit |
| exceptions, URL privée/signée, cookie, session, XML, traceback | Interdit | Interdit | Interdit | Interdit |
| absence de code/sources attestés | `source_inconnue` | Aucune cause inventée | Code fermé | Ne pas corréler |

Ce contrat **n'autorise aucun nouvel upload, prompt IA ou export automatique**.
La présence d'un fournisseur activé peut elle-même dévoiler une préférence de
l'utilisateur : les exports restent à déclenchement explicite. Une extension
Python exécutée *dans le même processus* n'est pas un bac à sable de sécurité ;
on empêche la publication accidentelle de champs plugin arbitraires, sans
revendiquer de garantie contre du code Python hostile.

## Portée et recette

Tests headless : 0/1/2 échecs, doublons d'un fournisseur, échec
`singleton_public`, slug non canonique et fournisseur inconnu, exception
`__str__` hostile, reconnexion et nettoyage des anciens états, refus de
publier le détail injecté via `Diagnostics.add`, plafond 40, export sans secrets.
L'UI **#2370** reste responsable d'utiliser les champs `connection_failures`
dans le bilan utilisateur/Diagnostics. #2380 porte le modèle commun *au-delà*
des connexions ; ne pas inférer de lien avec les erreurs ADEPT/Numilog.
