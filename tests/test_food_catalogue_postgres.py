"""Shared food boundaries, official seed integrity and independent snapshots."""
import json
from decimal import Decimal
from pathlib import Path
from app.cli import seed_foods, upgrade
from test_plans_postgres import PostgresTestCase


class FoodCatalogueTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        with self.connect_db() as conn:
            self.food = conn.execute("INSERT INTO food_catalogue(name,source,source_id,dataset_version,source_url,"
                "kcal_100g,protein_100g,carbs_100g,fat_100g) VALUES ('Test raw food','test','one','test',"
                "'https://example.org',123.456789,NULL,0,2) RETURNING catalogue_id").fetchone()[0]

    def tearDown(self):
        super().tearDown()
        with self.connect_db() as conn:
            conn.execute("DELETE FROM food_catalogue WHERE source='test'")

    def promote(self):
        with self.connect_db() as conn:
            conn.execute('UPDATE app_user SET is_admin=true WHERE user_id=%s', (self.owner,))

    def test_visibility_search_preparation_and_private_copy(self):
        self.post(self.a,'/nutrition/foods/new',name='Alice exclusive',kcal_100g=100)
        for client in [self.a,self.b]:
            page=client.get('/nutrition/foods?scope=all&q=Test').data
            self.assertIn(b'Test raw food',page)
            self.assertIn(b'Food library',page)
        self.assertNotIn(b'Alice exclusive',self.b.get('/nutrition/foods?scope=all').data)
        self.assertIn(b'Alice exclusive',self.a.get('/nutrition/foods?scope=all').data)
        raw=self.a.get('/nutrition/foods?scope=library&q=Rice&preparation=raw').data
        cooked=self.a.get('/nutrition/foods?scope=library&q=Rice&preparation=cooked').data
        self.assertIn(b'raw',raw); self.assertNotIn(b'enriched, cooked',raw)
        self.assertIn(b'enriched, cooked',cooked)
        response=self.post(self.a,f'/nutrition/library/{self.food}/copy')
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.b.get(response.location).status_code,404)
        with self.connect_db() as conn:
            row=conn.execute("SELECT kcal_100g,protein_100g,allergen_complete,provenance FROM nutrition_food WHERE user_id=%s AND provenance<>'{}'::jsonb",(self.owner,)).fetchone()
            self.assertEqual(row[:3],(Decimal('123.456789'),None,False))
            self.assertEqual(row[3]['source_id'],'one')

    def test_admin_routes_require_permissions_and_csrf(self):
        for path in ['/nutrition/library/admin',f'/nutrition/library/{self.food}/edit',f'/nutrition/library/{self.food}/status']:
            self.assertEqual(self.a.get(path).status_code,403)
            if not path.endswith('admin'):
                self.assertEqual(self.post(self.a,path,confirm='change').status_code,403)
        self.promote()
        self.assertEqual(self.a.get('/nutrition/library/admin').status_code,200)
        self.assertEqual(self.a.get(f'/nutrition/library/{self.food}/edit').status_code,200)
        self.assertEqual(self.a.post(f'/nutrition/library/{self.food}/status').status_code,400)
        self.assertEqual(self.post(self.a,f'/nutrition/library/{self.food}/status').status_code,400)
        self.assertEqual(self.post(self.a,f'/nutrition/library/{self.food}/status',confirm='change').status_code,302)
        self.assertEqual(self.b.get(f'/nutrition/library/{self.food}/log').status_code,404)
        self.assertEqual(self.post(self.b,f'/nutrition/library/{self.food}/copy').status_code,404)
        self.assertNotIn(b'Test raw food',self.b.get('/nutrition/foods?scope=library').data)
        self.assertIn(b'Test raw food',self.a.get('/nutrition/library/admin').data)

    def test_shared_logging_preserves_unknowns_provenance_and_snapshots(self):
        path=f'/nutrition/library/{self.food}/log'
        self.assertEqual(self.post(self.a,path,logged_on='2026-01-02',meal='breakfast',grams='33.33').status_code,302)
        with self.connect_db() as conn:
            before=conn.execute('SELECT food_name,grams,kcal_100g,protein_100g,provenance FROM nutrition_diary_entry WHERE user_id=%s',(self.owner,)).fetchone()
        self.assertEqual(before[2],Decimal('123.456789')); self.assertIsNone(before[3])
        self.promote()
        response=self.post(self.a,f'/nutrition/library/{self.food}/edit',name='Changed cooked food',preparation='cooked',
                           kcal_100g='999',protein_100g='1',carbs_100g='2',fat_100g='3',allergens='milk',allergen_complete='yes')
        self.assertEqual(response.status_code,302)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT food_name,grams,kcal_100g,protein_100g,provenance FROM nutrition_diary_entry WHERE user_id=%s',(self.owner,)).fetchone(),before)
            self.assertEqual(conn.execute('SELECT allergens,allergen_complete FROM food_catalogue WHERE catalogue_id=%s',(self.food,)).fetchone(),(['milk'],True))
        page=self.a.get('/nutrition?date=2026-01-02').data
        self.assertIn(b'Incomplete',page)
        self.assertIn(b'Test raw food',page); self.assertNotIn(b'Changed cooked food',page)

    def test_catalogue_validation_and_post_only_copy(self):
        self.promote()
        for value in ['NaN','Infinity','-1','1000.000001','0.0000001','1e2']:
            response=self.post(self.a,f'/nutrition/library/{self.food}/edit',name='Invalid',preparation='raw',kcal_100g=value)
            self.assertEqual(response.status_code,200)
        self.assertEqual(self.a.get(f'/nutrition/library/{self.food}/copy').status_code,405)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT name FROM food_catalogue WHERE catalogue_id=%s',(self.food,)).fetchone()[0],'Test raw food')

    def test_library_diary_snapshot_can_be_corrected_without_losing_precision_or_source(self):
        self.post(self.a,f'/nutrition/library/{self.food}/log',logged_on='2026-01-02',meal='lunch',grams=100)
        with self.connect_db() as conn:
            identifier=conn.execute('SELECT entry_id FROM nutrition_diary_entry WHERE user_id=%s',(self.owner,)).fetchone()[0]
        self.assertEqual(self.post(self.a,f'/nutrition/diary/2026-01-02/entries/{identifier}/edit',
            logged_on='2026-01-02',meal='lunch',grams=50,food_name='Corrected snapshot',kcal_100g='123.456789',
            protein_100g='',carbs_100g='0',fat_100g='2').status_code,302)
        with self.connect_db() as conn:
            rate,provenance=conn.execute('SELECT kcal_100g,provenance FROM nutrition_diary_entry WHERE entry_id=%s',(identifier,)).fetchone()
            self.assertEqual(rate,Decimal('123.456789'))
            self.assertTrue(provenance['manually_edited']);self.assertEqual(provenance['source_id'],'one')

    def test_bundled_seed_is_exact_attributed_and_repeatable(self):
        data=json.loads((Path(__file__).resolve().parents[1]/'db/data/usda-sr-2018.json').read_text())
        self.assertEqual(len(data['foods']),100)
        self.assertEqual(len({row['source_id'] for row in data['foods']}),100)
        self.assertEqual(len(data['archive_sha256']),64)
        with self.connect_db() as conn:
            for food in data['foods']:
                row=conn.execute('SELECT name,kcal_100g,protein_100g,carbs_100g,fat_100g,allergen_complete,allergens,source_url FROM food_catalogue WHERE source_id=%s AND source=%s',(food['source_id'],data['source'])).fetchone()
                self.assertEqual(row[0],food['name'])
                self.assertEqual(row[1:5],tuple(None if food[key] is None else Decimal(food[key]) for key in ['kcal_100g','protein_100g','carbs_100g','fat_100g']))
                self.assertEqual(row[5:7],(False,[]))
                self.assertEqual(row[7],food['source_url'])
            identifier=conn.execute("SELECT catalogue_id FROM food_catalogue WHERE source_id='171265'").fetchone()[0]
            original=conn.execute('SELECT name,is_active FROM food_catalogue WHERE catalogue_id=%s',(identifier,)).fetchone()
            try:
                conn.execute("UPDATE food_catalogue SET name='Administrator change',is_active=false WHERE catalogue_id=%s",(identifier,))
                before=conn.execute('SELECT * FROM food_catalogue ORDER BY catalogue_id').fetchall()
                seed_foods(conn); seed_foods(conn)
                self.assertEqual(conn.execute('SELECT * FROM food_catalogue ORDER BY catalogue_id').fetchall(),before)
                self.assertEqual(upgrade(conn),[])
            finally:
                conn.execute('UPDATE food_catalogue SET name=%s,is_active=%s WHERE catalogue_id=%s',(*original,identifier))
        result=self.app.test_cli_runner().invoke(args=['seed-foods'])
        self.assertEqual(result.exit_code,0,result.output)
