# Service LLM Core (optionnel, expérimental)

Aucun fournisseur de modèle, appel réseau ni clé n'est requis pour utiliser Cifereca.
Par défaut `llm.enabled=false` et `context.service("llm") is None` : les
plugins **doivent** conserver leur fonctionnement complet hors LLM.

## Exemple local explicite (serveur compatible OpenAI déjà installé)

Dans le fichier de configuration Core JSON :

```json
{
  "llm": {
    "enabled": true,
    "provider": "openai-compatible",
    "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
    "model": "modele-local",
    "allow_remote": false,
    "api_key_env": "",
    "timeout_seconds": 5,
    "max_input_chars": 4000,
    "max_output_tokens": 256
  }
}
```

La configuration ci-dessus **ne lance pas** de serveur ; elle autorise seulement
un appel lorsqu'un plugin le demande explicitement. L'endpoint doit être un
serveur parlant l'API de chat completions compatible OpenAI. Le Core ne dépend
d'aucune bibliothèque fournisseur. Les seuls hôtes reconnus comme locaux
sont `localhost` et les adresses IP loopback.

Pour un endpoint distant, configurer **explicitement**
`enabled=true`, une URL HTTPS et `allow_remote=true`.
C'est l'autorisation d'envoyer les prompts/documents aux systèmes distants.
Les redirections HTTP et les URL comportant des identifiants, query strings ou
fragments sont refusées. Ne pas inscrire de token dans l'URL ou la configuration :
`api_key_env` désigne **le nom** d'une variable d'environnement déjà définie.

## Plugin

```python
from cifereca.plugin_api import LLMRequest

llm = context.service("llm")
if llm is not None and llm.available():
    result = llm.generate(
        LLMRequest("Résume ce bref extrait déjà sélectionné", max_output_tokens=100),
        control=control,  # facultatif : TaskControl
    )
    if result.ok:
        print(result.text)  # contenu généré, non attesté
```

`LLMResult` indique `generated=True`, `provider`, `model`, `remote` et,
en mode `task="structured"`, un objet JSON dans `structured`. Aucun résultat
LLM ne fait foi pour les âges attestés, la provenance bibliographique, le DRM,
la propriété ou les droits d'accès.

Un backend indisponible, une annulation ou un timeout retourne `ok=False`
avec un `reason` non sensible : **jamais de prompt, réponse, exception HTTP
ou secret**. Une file bornée de deux workers permet de ne pas bloquer le Core ;
une requête réseau déjà lancée ne peut pas être interrompue physiquement et
peut continuer au plus jusqu'à la fin de son thread daemon. L'appelant reçoit
cependant un timeout/une annulation sans attendre la fin de ce thread. Le
timeout maximum est 15 secondes, la sortie est limitée à 32 KiB et le prompt
à 16 000 caractères. Aucun retry, embedding ou outil autonome en v1.

Pour les plugins utilisant GTK, lancer `generate` via le TaskRunner/broker de
tâches, **jamais sur le fil principal GTK**.
