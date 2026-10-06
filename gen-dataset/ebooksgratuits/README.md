# Pseudo service Ebooks libres et gratuits

Service HTML local pour tester `provider.ebooksgratuits` sans accès Internet.

Il couvre la recherche par titre, auteur, catégorie et genre ainsi que le
téléchargement d'un EPUB valide. Les livres sont fictifs et déterministes.

Lancement :

```bash
python3 gen-dataset/ebooksgratuits/pseudo_ebooksgratuits_service.py --books 500
```

Configuration du plugin :

```
base_url = http://127.0.0.1:8096/
```

Le profil `ux-ebooksgratuits` configure automatiquement ce service.
