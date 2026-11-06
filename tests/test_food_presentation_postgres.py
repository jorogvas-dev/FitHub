"""Numeric sorting, member provenance visibility and full-precision edit values."""
from decimal import Decimal as D
from flask import template_rendered
from test_plans_postgres import PostgresTestCase


class FoodPresentationTests(PostgresTestCase):
    def page(self, path):
        contexts = []
        def capture(sender, template, context, **extra):
            contexts.append(context)
        with template_rendered.connected_to(capture, self.app):
            response = self.a.get(path)
        self.assertEqual(response.status_code, 200)
        return response, contexts[-1]

    def test_all_four_sorts_both_directions_unknowns_last_ties_and_filters(self):
        with self.connect_db() as conn:
            for name, value in [('Zulu', '2.49'), ('Alpha', '2.40'), ('Tie', '2.40'),
                                ('Tie', '2.40'), ('Ten', '10'), ('Zero', '0'), ('Missing', None)]:
                conn.execute('INSERT INTO nutrition_food(user_id,name,kcal_100g,protein_100g,carbs_100g,fat_100g) '
                             'VALUES (%s,%s,%s,%s,%s,%s)',
                             (self.owner, 'Sort '+name, value, value, value, value))
            conn.execute("INSERT INTO food_catalogue(name,source,source_id,dataset_version,source_url,preparation,"
                         "kcal_100g,protein_100g,carbs_100g,fat_100g) VALUES "
                         "('Sort Shared','presentation','one','test','https://example.org','raw',1,1,1,1),"
                         "('Sort Cooked','presentation','two','test','https://example.org','cooked',99,99,99,99)")
        for field in ['kcal', 'protein', 'carbs', 'fat']:
            orders = {}
            for direction in ['asc', 'desc']:
                path = f'/nutrition/foods?scope=all&q=Sort&preparation=raw&date=2026-01-02&sort={field}&direction={direction}'
                response, context = self.page(path)
                rows = context['foods']
                self.assertEqual(len(rows), 8)
                self.assertEqual(rows[-1]['name'], 'Sort Missing')
                known = rows[:-1]
                self.assertEqual([r[field+'_100g'] for r in known],
                                 sorted([D('2.49'),D('2.40'),D('2.40'),D('2.40'),D(10),D(0),D(1)], reverse=direction=='desc'))
                orders[direction] = [r['token'] for r in rows if r[field+'_100g'] == D('2.40')]
                self.assertEqual(context['query'], 'Sort')
                self.assertEqual(context['scope'], 'all')
                self.assertEqual(context['preparation'], 'raw')
                self.assertIn(b'name="date" value="2026-01-02"', response.data)
                self.assertIn(f'<option value="{field}" selected>'.encode(), response.data)
                self.assertNotIn(b'Sort Cooked', response.data)
                self.assertNotIn(b'https://example.org', response.data)
            self.assertEqual(orders['asc'], orders['desc'])
        self.assertNotIn(b'Sort Zero', self.b.get('/nutrition/foods?scope=all&q=Sort').data)
        for query in ['sort=source_url', 'sort=kcal_100g', 'direction=sideways']:
            self.assertEqual(self.a.get('/nutrition/foods?'+query).status_code, 400)

    def test_member_library_has_no_citations_admin_and_database_keep_provenance(self):
        with self.connect_db() as conn:
            food = conn.execute("SELECT catalogue_id,source_url FROM food_catalogue WHERE source_id='171265'").fetchone()
        for path in ['/nutrition/foods?scope=library', f'/nutrition/library/{food[0]}/log']:
            response, _ = self.page(path)
            self.assertNotIn(b'fdc.nal.usda.gov', response.data)
            self.assertNotIn(b'Copied nutrition source:', response.data)
            self.assertNotIn(b'FDC ', response.data)
            self.assertNotIn(b'target="_blank"', response.data)
        with self.connect_db() as conn:
            conn.execute('UPDATE app_user SET is_admin=true WHERE user_id=%s', (self.owner,))
        self.assertIn(food[1].encode(), self.a.get(f'/nutrition/library/{food[0]}/edit').data)
        self.assertEqual(self.post(self.a, f'/nutrition/library/{food[0]}/copy').status_code, 302)
        with self.connect_db() as conn:
            copy = conn.execute('SELECT food_id,kcal_100g,provenance FROM nutrition_food WHERE user_id=%s', (self.owner,)).fetchone()
            self.assertEqual(copy[2]['source_url'], food[1])
        edit = self.a.get(f'/nutrition/foods/{copy[0]}/edit')
        self.assertIn(('value="'+str(copy[1])+'"').encode(), edit.data)
        self.assertNotIn(b'fdc.nal.usda.gov', edit.data)

    def test_diary_rounds_display_only_and_keeps_edit_values_and_incomplete_zero(self):
        day = '2026-01-02'
        for name, protein in [('Known', '0'), ('Unknown', '')]:
            self.assertEqual(self.post(self.a, f'/nutrition/diary/{day}/entries/new', logged_on=day,
                             meal='lunch', grams='100.50', food_name=name, kcal_100g='2.50',
                             protein_100g=protein, carbs_100g='0', fat_100g='1.50').status_code, 302)
        response, context = self.page('/nutrition?date='+day)
        self.assertEqual(context['totals']['kcal']['total'], D('5.025'))
        self.assertIn(b'101 g', response.data)
        self.assertIn(b'Incomplete', response.data)
        self.assertIsNone(context['totals']['protein']['total'])
        self.assertEqual(context['totals']['protein']['subtotal'], D(0))
        with self.connect_db() as conn:
            entry = conn.execute('SELECT entry_id,grams,kcal_100g FROM nutrition_diary_entry WHERE user_id=%s ORDER BY entry_id', (self.owner,)).fetchone()
            self.assertEqual(entry[1:], (D('100.50'), D('2.50')))
        edit = self.a.get(f'/nutrition/diary/{day}/entries/{entry[0]}/edit')
        self.assertIn(b'value="100.50"', edit.data)
        self.assertIn(b'value="2.500000"', edit.data)
