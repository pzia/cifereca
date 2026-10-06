# Pseudo service BnF Catalogue SRU

Serveur SRU 1.2 local pour tester `provider.bnf-catalogue`.

Par défaut :

- 20 000 notices ;
- supports physiques, numériques et indéterminés ;
- recherche CQL sur titre, auteur, ISBN, sujet et texte libre ;
- séries et collections synthétiques via `dc:relation` / `dc:source`, recherchables par le plugin ;
- pagination SRU ;
- liens Gallica sur une partie des notices numériques ;
- diagnostics SRU pour requêtes invalides ;
- cache SQLite persistant ;
- métadonnées générées via `gen-dataset/common/fake_books.py`.

Lancement :

```bash
python3 gen-dataset/bnf/pseudo_bnf_sru_service.py
```

Configuration du plugin :

```
sru_url = http://127.0.0.1:8095/api/SRU
```

Le plugin BnF accepte déjà une URL SRU configurable : aucune modification du
plugin n'est nécessaire.
