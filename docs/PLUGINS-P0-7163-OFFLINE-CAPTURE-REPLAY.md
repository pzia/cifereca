# Numilog #7163 — rejouer une capture HTTP existante, hors réseau

Le ticket #7163 conserve **30 échecs de parsing de layout du 20/11/2026 non qualifiés individuellement**. Les assertions sur fixtures ne permettent pas de les classer. Le format **déjà exporté** par `capture_session_export` (`http/index.json` et `http/bodies/<ref>-response.bin`) peut être analysé localement sans refaire de GET et sans transférer les corps privés.

Depuis le dépôt :

```sh
PYTHONPATH=src/plugins/provider-numilog python3 -m cifereca_numilog_capture_replay /chemin/local/capture.zip
```

Le résultat JSON expose uniquement des **comptes fermés** : GET publics admissibles, signatures de transport exactement répétées, intervalles observés qui se chevauchent, pages acceptées par le parseur partagé et raisons de refus. Les corps Brotli `br` sont décodés en **tranches bornées** si le paquet optionnel `brotli` est déjà disponible ; sinon ils restent « encoding_unavailable_or_invalid », **jamais** présentés comme zéro résultat. Gzip et deflate sont aussi gérés. Rien n'est envoyé, collecté, écrit ou publié. La capture privée et ses URLs, en-têtes et corps ne sont pas reproduits dans le rapport.

Seuls les GET `provider.numilog.public` **sans Cookie/Authorization ni corps** et à URL correspondant au parseur public sont comptés. Deux GET de même URL à des instants distincts sont *répétés*, mais seuls des intervalles horaires observés chevauchants figurent comme candidats de coalescence. Une capture partielle, un codec absent, une réponse invalide ou une ambiguïté fournisseur ne peuvent pas valoir succès. Les compteurs hors réseau ne prouvent pas que les 30 cas ont été reçus : rapprocher ensuite les comptes fermés du journal source **sur la machine qui possède déjà la capture** et consigner les raisons effectives dans #7163.
