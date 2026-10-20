# Pseudo service Ebooks libres et gratuits

Service HTML local pour tester `provider.ebooksgratuits` sans accès Internet.

Il couvre la recherche par titre, auteur, catégorie et genre ainsi que le
téléchargement d'un EPUB valide. Les livres sont fictifs et déterministes.

Lancement :

```bash
python3 simulation/ebooksgratuits/pseudo_ebooksgratuits_service.py --books 500
```

Configuration du plugin :

```
base_url = http://127.0.0.1:8096/
```

Le profil `ux-ebooksgratuits` configure automatiquement ce service.

## P0 #771 — erreurs HTTP explicites

Les endpoints existants (GET / et /ebooks.php, POST /ebooks.php,
GET /details.php?id=1 et /newsendbook.php?id=1) acceptent un paramètre
optionnel ux_fault :

- Recherche et fiche : 429 (Retry-After: 3), 503, login_html (HTML login 200),
  malformed_html (HTML incomplet 200).
- Téléchargement : 429, 503, expired_link (410), wrong_mime ou missing_format
  (HTML 200), invalid_epub (octets invalides avec MIME EPUB).
- Paramètre absent/inconnu : contrat inchangé. /health reste sain.

Le profil ux-errors active ce service local, mais aucune erreur n'est injectée
sans le paramètre explicite. Les fautes n'écrivent pas d'EPUB et n'activent ni
réseau externe, ni compte réel, ni acquisition. La validation HTTP est dans
simulation/tests/test_ebooksgratuits_negative_protocol_p0.py ; elle ne remplace
pas une recette GTK.
