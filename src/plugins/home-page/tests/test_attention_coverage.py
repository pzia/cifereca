from __future__ import annotations
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("cifereca_home",ROOT/"cifereca_home.py")
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class AttentionCoverageTests(unittest.TestCase):
    def test_source_groups_keep_local_public_and_authenticated_distinct(self):
        row={
            "providers":["local","gutenberg","paris"],
            "sources":[
                {"provider_id":"local","label":"Local","connected":False},
                {"provider_id":"gutenberg","label":"Gutenberg","connected":False},
                {"provider_id":"paris","label":"Paris","connected":False},
            ],
            "statistics":{"local_books":12,"sources":{"local":12,"gutenberg":3,"paris":2}},
        }
        groups=mod.source_groups(row)
        self.assertEqual([x["provider_id"] for x in groups["local"]],["local"])
        self.assertEqual([x["provider_id"] for x in groups["public"]],["gutenberg"])
        self.assertEqual([x["provider_id"] for x in groups["authenticated"]],["paris"])
        self.assertEqual(groups["local"][0]["count"],12)

    def test_local_and_public_sources_never_become_connect_actions(self):
        row={
            "providers":["local","gutenberg"],
            "sources":[
                {"provider_id":"local","label":"Local","connected":False},
                {"provider_id":"gutenberg","label":"Gutenberg","connected":False},
            ],
            "statistics":{"known_loans":0,"local_books":8},
            "sync":{},
        }
        labels=[item["label"] for item in mod.attention_items(row,now=100)]
        self.assertFalse(any("connexion requise" in label for label in labels))

    def test_attention_deduplicates_sync_and_stale_loans_and_orders_priority(self):
        row={
            "providers":["paris"],
            "sources":[{"provider_id":"paris","label":"Paris","connected":False}],
            "statistics":{"known_loans":2,"loans_observed_at":100},
            "sync":{"attention_required":2,"errors":1},
        }
        items=mod.attention_items(row,now=100+25*3600)
        keys=[item["key"] for item in items]
        self.assertIn("sync",keys)
        self.assertNotIn("loans-stale",keys)
        self.assertEqual(items[0]["key"],"sync")
        self.assertEqual(items[0]["action_label"],"Réessayer")
        source=next(item for item in items if item["key"]=="source:paris")
        self.assertEqual(source["action"],"settings")
        self.assertEqual(source["action_label"],"Connecter")

    def test_attention_aggregates_known_actions(self):
        row={
            "providers":["paris"],
            "sources":[{"provider_id":"paris","label":"Paris","connected":False}],
            "statistics":{"known_loans":2,"loans_observed_at":100,"due_soon_loans":1,"available_bookings":2},
            "sync":{"attention_required":3,"errors":1},
        }
        labels=[item["label"] for item in mod.attention_items(row,now=100+25*3600)]
        self.assertIn("Prêts proches de l’échéance",labels)
        self.assertIn("Réservations disponibles",labels)
        self.assertNotIn("Inventaire des prêts à actualiser",labels)
        self.assertIn("Synchronisation à reprendre",labels)
        self.assertIn("Paris · connexion requise",labels)
        self.assertIn("Profil ADE à associer",labels)


class CompactDashboardTests(unittest.TestCase):
    def test_counts_are_not_duplicated_as_another_local_statistic(self):
        source={'label':'Local','provider_id':'local','kind':'local','count':562}
        self.assertIn('déjà comptée',mod.compact_source_text(source,total_local_books=562))
        self.assertNotIn('562',mod.compact_source_text(source,total_local_books=562))
        self.assertIn('42 livre(s) d’origine',mod.compact_source_text(
            dict(source,count=42),total_local_books=562))

    def test_grouping_keeps_public_auth_and_unknown_origins_distinct(self):
        row={
            'providers':['local','gutenberg','paris'],
            'sources':[{'provider_id':'paris','label':'Paris','connected':False},
                       {'provider_id':'gutenberg','label':'Gutenberg','connected':False}],
            'statistics':{'local_books':562,
                          'sources':{'local':562,'gutenberg':80,'paris':24,'orphan-provider':9}},
        }
        groups=mod.compact_source_groups(row)
        self.assertEqual([r['provider_id'] for r in groups['local']],['local'])
        self.assertEqual([r['provider_id'] for r in groups['public']],['gutenberg'])
        self.assertEqual([r['provider_id'] for r in groups['authenticated']],['paris'])
        self.assertEqual([r['provider_id'] for r in groups['unknown']],['orphan-provider'])
        self.assertIn('catalogue public',mod.compact_source_text(
            {'label':'Gallica','kind':'public'}))
        self.assertIn('non connectée',mod.compact_source_text(groups['authenticated'][0]))
        self.assertNotIn('connect',mod.compact_source_text(groups['public'][0]))

    def test_sync_never_assumes_unknown_means_zero(self):
        self.assertEqual(mod.compact_sync_text({}),
            'État : Jamais synchronisée · acquisitions : — · actions requises : — · erreurs : —')
        self.assertEqual(
            mod.compact_sync_text({'status':'partial','new_acquisitions':0,
                                   'attention_required':2,'errors':1}),
            'État : Partielle · acquisitions : 0 · actions requises : 2 · erreurs : 1',
        )
        self.assertIn('erreurs : —',mod.compact_sync_text({'errors':None}))
        self.assertIn('erreurs : —',mod.compact_sync_text({'errors':False}))

    def test_attention_preserves_the_first_highest_priority_sync_action(self):
        row={
            'providers':['paris'],
            'sources':[{'provider_id':'paris','connected':False}],
            'statistics':{'known_loans':None},
            'sync':{'attention_required':2,'errors':1,'status':'partial'},
        }
        items=mod.attention_items(row,now=0)
        self.assertEqual(items[0]['key'],'sync')
        self.assertEqual(items[0]['action_label'],'Réessayer')
        self.assertNotIn('loans-stale',[item['key'] for item in items])

if __name__=="__main__":
    unittest.main()


class StoreVsCatalogueProjectionTests(unittest.TestCase):
    def test_numilog_public_is_not_a_private_login_or_owned_book(self):
        row={
            'providers':['numilog','numilog-catalog'],
            'sources':[
                {'provider_id':'numilog','label':'Numilog','connected':True},
                {'provider_id':'numilog-catalog','label':'Numilog','connected':False},
            ],
            'statistics':{'sources':{'numilog':4,'numilog-catalog':18},
                          'known_loans':0,'remote_owned_rights':2},
            'sync':{},
        }
        groups=mod.compact_source_groups(row)
        self.assertEqual([s['provider_id'] for s in groups['public']], ['numilog-catalog'])
        self.assertEqual([s['provider_id'] for s in groups['authenticated']], ['numilog'])
        public=mod.compact_source_text(groups['public'][0])
        private=mod.compact_source_text(groups['authenticated'][0])
        self.assertIn('Catalogue public',public)
        self.assertIn('sans achat attesté',public)
        self.assertNotIn('connexion',public)
        self.assertIn('Mes achats',private)
        self.assertIn('connectée',private)
        self.assertIn('achats non présumés',private)
        # No public anonymous catalogue should prompt a credential login.
        attention=mod.attention_items(row,now=100)
        self.assertFalse(any(x['key']=='source:numilog-catalog' for x in attention))

    def test_store_only_has_no_loan_source_and_no_false_local_artifact(self):
        row={'providers':['kobo','numilog'],
             'sources':[{'provider_id':'kobo','connected':True},
                        {'provider_id':'numilog','connected':True}],
             'statistics':{'known_loans':None,'remote_owned_rights':7,
                           'local_books':0,'sources':{'kobo':2,'numilog':5}},
             'sync':{}}
        groups=mod.compact_source_groups(row)
        self.assertFalse(groups['public'])
        self.assertTrue(all('Mes achats' in mod.compact_source_text(x)
                            for x in groups['authenticated']))
        summary=mod.overview_counts_lines(row['statistics'],row=row)
        self.assertIn('aucune source de prêt configurée',summary[0])
        self.assertIn('Livres locaux : 0',summary[0])
        self.assertIn('Droits d’achat distants : 7',summary[1])
        self.assertFalse(any(x['key']=='loans-stale' for x in mod.attention_items(row,now=100)))

    def test_paris_and_store_remain_separate_loan_and_purchase_usages(self):
        row={'providers':['paris','kobo','numilog-catalog'],
             'sources':[{'provider_id':'paris','connected':True},
                        {'provider_id':'kobo','connected':True},
                        {'provider_id':'numilog-catalog','connected':False}],
             'statistics':{'known_loans':3,'remote_owned_rights':4,
                           'local_books':1,'sources':{'paris':3,'kobo':4,
                                                     'numilog-catalog':8}},
             'sync':{}}
        groups=mod.compact_source_groups(row)
        self.assertEqual(len(groups['public']),1)
        self.assertEqual(len(groups['authenticated']),2)
        labels={x['provider_id']:mod.compact_source_text(x)
                for x in groups['authenticated']}
        self.assertIn('Mes achats',labels['kobo'])
        self.assertNotIn('Mes achats',labels['paris'])
        self.assertIn('prêts de bibliothèque connus : 3',
                      mod.overview_counts_lines(row['statistics'],row=row)[0])
        self.assertIn('Droits d’achat distants : 4',
                      mod.overview_counts_lines(row['statistics'],row=row)[1])

    def test_public_numilog_with_zero_indexed_books_does_not_infer_zero_offers(self):
        item={'provider_id':'numilog-catalog','kind':'public','count':0}
        text=mod.compact_source_text(item)
        self.assertIn('0 livre(s) d’origine',text)
        self.assertIn('sans achat attesté',text)
        self.assertNotIn('Mes achats',text)
