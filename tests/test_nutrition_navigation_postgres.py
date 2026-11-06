"""Selected-date navigation through the actual private/shared food logging flows."""
from urllib.parse import parse_qs, urlsplit

from test_dashboard_postgres import Links
from test_plans_postgres import PostgresTestCase

DAY = '2026-01-02'


class NutritionNavigationTests(PostgresTestCase):
    def links(self, response, text):
        self.assertEqual(response.status_code, 200)
        return [link for link in Links(response.get_data(as_text=True)).links if link['text'] == text]

    def assert_date(self, link):
        self.assertEqual(parse_qs(urlsplit(link['href']).query)['date'], [DAY])

    def test_primary_log_food_and_both_collections_retain_selected_date(self):
        diary = self.a.get('/nutrition?date='+DAY)
        action = next(link for link in self.links(diary, 'Log food')
                      if 'primary-log-food' in link.get('class', ''))
        self.assertEqual(urlsplit(action['href']).path, '/nutrition/foods')
        self.assert_date(action)
        selection = self.a.get(action['href'])
        self.assertIn(('name="date" value="'+DAY+'"').encode(), selection.data)
        for label in ['Daily diary', 'Food library', 'My foods']:
            for link in self.links(selection, label):
                self.assert_date(link)
                self.assertEqual(self.a.get(link['href']).status_code, 200)
        self.assertNotIn(b'Manage shared food library', selection.data)

    def test_private_food_back_cancel_and_submit_return_to_selected_diary(self):
        self.assertEqual(self.post(self.a, '/nutrition/foods/new', name='Partial label',
                                  kcal_100g=250, protein_100g='', carbs_100g=20, fat_100g=5).status_code, 302)
        with self.connect_db() as conn:
            food = conn.execute('SELECT food_id FROM nutrition_food WHERE user_id=%s', (self.owner,)).fetchone()[0]
        selection = self.a.get('/nutrition/foods?scope=mine&date='+DAY)
        log = self.links(selection, 'Log food')[0]
        self.assert_date(log)
        form = self.a.get(log['href'])
        for label in ['Back to food selection', 'Cancel', '← Back to daily diary']:
            for link in self.links(form, label):
                self.assert_date(link)
        self.assertIn(b'Unknown', form.data)
        self.assertIn(b'unknown / incomplete', form.data)
        self.assertIn(('name="logged_on" required min="1900-01-01" max="2100-12-31" value="'+DAY+'"').encode(), form.data)
        response = self.post(self.a, log['href'], logged_on=DAY, meal='dinner', grams=125)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/nutrition?date='+DAY)
        self.assertEqual(self.post(self.a, '/nutrition/settings', timezone='Europe/Sofia', target_protein=100).status_code, 302)
        diary = self.a.get(response.location)
        self.assertIn(b'Partial label', diary.data)
        self.assertIn(b'Remaining unknown', diary.data)
        self.assertIn(b'Unknown', diary.data)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT logged_on::text,meal,food_id FROM nutrition_diary_entry WHERE user_id=%s',
                                          (self.owner,)).fetchall(), [(DAY, 'dinner', food)])

    def test_library_logging_keeps_date_attribution_and_snapshot_notice(self):
        selection = self.a.get('/nutrition/foods?scope=library&q=Rice&date='+DAY)
        log = self.links(selection, 'Log food')[0]
        self.assert_date(log)
        page = self.a.get(log['href'])
        self.assertNotIn(b'fdc.nal.usda.gov', page.data)
        self.assertIn(b'Nutrition data is not allergen verification', page.data)
        self.assertIn(b'Later food edits will not change it', page.data)
        for link in self.links(page, 'Daily diary') + self.links(page, 'Back to food selection'):
            self.assert_date(link)
        self.assertEqual(self.post(self.a, log['href'], logged_on=DAY, meal='lunch', grams=100).status_code, 302)
        diary = self.a.get('/nutrition?date='+DAY)
        self.assertIn(b'Nutrition source', diary.data)
        self.assertIn(b'fdc.nal.usda.gov', diary.data)
        self.assertIn(b'recorded snapshot', diary.data)
        self.assertIn(b'guarantee', diary.data)

    def test_meal_plan_navigation_and_empty_states_do_not_log_anything(self):
        for path in ['/nutrition', '/nutrition/foods', '/nutrition/meal-plans', '/nutrition/meal-plans/new']:
            response = self.a.get(path)
            self.assertEqual(response.status_code, 200)
            for label in ['Daily diary', 'Food library', 'My foods', 'Recipes', 'Meal plans', 'Preferences & targets']:
                self.assertTrue(self.links(response, label), (path, label))
        self.assertIn(b'No meal plans yet', self.a.get('/nutrition/meal-plans').data)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM nutrition_diary_entry').fetchone()[0], 0)
