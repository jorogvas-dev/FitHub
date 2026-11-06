"""Real user dashboard data, existing action flows and shared navigation privacy."""
from datetime import date, datetime, timezone
from decimal import Decimal as D
from html.parser import HTMLParser
from unittest.mock import patch

from flask import template_rendered

from test_plans_postgres import PostgresTestCase

DAY = '2026-06-11'


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.current = dict(attrs, text='')

    def handle_data(self, data):
        if self.current is not None:
            self.current['text'] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            self.current['text'] = ' '.join(self.current['text'].split())
            self.links.append(self.current)
            self.current = None


class DashboardTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        clock = patch('app.services.nutrition.utc_now', return_value=datetime(2026, 6, 10, 22, 30, tzinfo=timezone.utc))
        clock.start()
        self.addCleanup(clock.stop)

    def dashboard(self, client=None, suffix=''):
        captured = []
        def capture(sender, template, context, **extra):
            captured.append(context)
        with template_rendered.connected_to(capture, self.app):
            response = (client or self.a).get('/dashboard'+suffix)
        self.assertEqual(response.status_code, 200)
        return response, captured[-1]

    def settings(self, client=None, **overrides):
        values = dict(timezone='Europe/Sofia', target_kcal=2200, target_protein=140, target_carbs=250, target_fat=70)
        self.assertEqual(self.post(client or self.a, '/nutrition/settings', **{**values, **overrides}).status_code, 302)

    def food(self, client=None, day=DAY, **overrides):
        values = dict(logged_on=day, meal='lunch', grams=100, food_name='Private diary food',
                      kcal_100g=200, protein_100g=10, carbs_100g=20, fat_100g=5)
        self.assertEqual(self.post(client or self.a, f'/nutrition/diary/{day}/entries/new', **{**values, **overrides}).status_code, 302)

    def test_empty_defaults_render_without_creating_records_or_sample_values(self):
        with self.connect_db() as conn:
            before = {table: conn.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall()
                      for table in ['app_user','fitness_profile','nutrition_settings','nutrition_diary_entry','body_measurement','workout_plan','workout_session','meal_plan']}
        response, context = self.dashboard()
        self.assertEqual(context['plans'], 0)
        self.assertEqual(context['workouts'], 0)
        self.assertIsNone(context['training_day'])
        self.assertIsNone(context['meal_plan'])
        self.assertEqual(context['food_count'], 0)
        self.assertEqual(context['today'], date(2026, 6, 11))
        for value in context['nutrition_totals'].values():
            self.assertEqual(value['total'], D(0))
            self.assertIsNone(value['target'])
        for expected in [b'Your first plan starts here',b'No food logged today',b'Target not set',b'No measurements recorded.',b'No saved meal plan',b'Finish setup']:
            self.assertIn(expected, response.data)
        self.assertNotIn(b'Sample data', response.data)
        self.assertNotIn(b'Alex', response.data)
        self.assertNotIn(b'2026-11-05', response.data)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        links = Links(response.get_data(as_text=True)).links
        self.assertTrue(all(row.get('href','').startswith('/') for row in links if row['text'] != 'Skip to content'))
        with self.connect_db() as conn:
            for table, rows in before.items():
                self.assertEqual(conn.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall(), rows, table)

    def test_real_training_preview_and_start_requires_existing_plan_day_post(self):
        plan = self.create_plan('My actual block'); day = self.create_day(plan, 'Actual upper day')
        self.create_entry(plan, day); self.create_entry(plan, day)
        response, context = self.dashboard()
        self.assertEqual(context['training_day']['plan_id'], plan)
        self.assertEqual(context['training_day']['day_id'], day)
        self.assertEqual(context['planned_sets'], 8)
        self.assertEqual(len(context['prescriptions']), 2)
        self.assertIn(b'My actual block', response.data)
        self.assertIn(b'Actual upper day', response.data)
        start = next(row for row in Links(response.get_data(as_text=True)).links if row['text'] == 'Start workout')
        self.assertEqual(start['href'], '/plans')
        self.assertIn(f'href="/plans/{plan}"'.encode(), self.a.get(start['href']).data)
        page = self.a.get(f'/plans/{plan}')
        target = f'/workouts/start/{plan}/{day}'
        self.assertIn(target.encode(), page.data)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM workout_session WHERE user_id=%s',(self.owner,)).fetchone()[0], 0)
        self.assertEqual(self.a.get(target).status_code, 405)
        self.assertEqual(self.a.post(target).status_code, 400)
        self.assertEqual(self.post(self.a, target).status_code, 302)
        self.assertEqual(self.dashboard()[1]['workouts'], 1)

    def test_all_cards_scope_to_member_even_for_forged_user_or_day_parameters(self):
        plan = self.create_plan('Own block'); self.create_day(plan, 'Own day')
        foreign = self.create_plan('Hidden second member block', self.b)
        self.post(self.b, f'/plans/{foreign}/days/new', name='Hidden second member day')
        self.settings(); self.settings(self.b, target_kcal=19999)
        self.food(kcal_100g=123); self.food(self.b, kcal_100g=999)
        with self.b.session_transaction() as state:
            other_id = state['user_id']
        with self.connect_db() as conn:
            conn.execute("INSERT INTO body_measurement(user_id,measured_on,weight_kg) VALUES (%s,'2026-01-01',499.99)",(other_id,))
            foreign_plan = conn.execute("INSERT INTO meal_plan(user_id,planned_on,targets,restrictions) VALUES (%s,%s,'{}','{}') RETURNING plan_id",(other_id,DAY)).fetchone()[0]
            conn.execute('UPDATE app_user SET is_admin=true WHERE user_id=%s',(self.owner,))
        response, context = self.dashboard(suffix=f'?user_id={other_id}&day_id=999999')
        self.assertEqual(context['plans'], 1)
        self.assertEqual(context['training_day']['plan_name'], 'Own block')
        self.assertEqual(context['nutrition_totals']['kcal']['total'], D('123'))
        self.assertIsNone(context['measurement'])
        self.assertIsNone(context['meal_plan'])
        for text in [b'Hidden second member', b'499.99', b'19999',f'href="/nutrition/meal-plans/{foreign_plan}"'.encode()]:
            self.assertNotIn(text, response.data)

    def test_missing_nutrients_keep_subtotals_unknowns_and_overage_lower_bound(self):
        self.settings(target_kcal=150)
        self.food(kcal_100g=200, carbs_100g='')
        self.food(kcal_100g='', protein_100g=0, carbs_100g='', fat_100g=0)
        response, context = self.dashboard()
        self.assertEqual(context['incomplete_entries'], 2)
        kcal = context['nutrition_totals']['kcal']
        self.assertIsNone(kcal['total'])
        self.assertEqual(kcal['subtotal'], D(200))
        self.assertIsNone(kcal['remaining'])
        self.assertEqual(kcal['over'], D(50))
        self.assertEqual(context['nutrition_totals']['protein']['remaining'], D(130))
        self.assertEqual(context['nutrition_totals']['carbs']['known_count'], 0)
        for text in [b'Incomplete diary',b'known subtotal',b'Remaining unknown',b'At least 50 kcal over target',b'Unknown']:
            self.assertIn(text, response.data)
        self.assertEqual(response.data.count(b'class="macro-track '), 2)

    def test_zero_targets_and_final_only_decimal_rounding_remain_correct(self):
        self.settings(target_kcal=0, target_protein=0, target_carbs=0, target_fat=0)
        for _ in range(3):
            self.food(grams='33.33', kcal_100g='0.01', protein_100g=0, carbs_100g=0, fat_100g=0)
        response, context = self.dashboard()
        self.assertEqual(context['nutrition_totals']['kcal']['total'], D('0.009999'))
        self.assertIn(b'Less than 1 kcal over target', response.data)
        self.assertNotIn(b'macro-track', response.data)
        self.assertEqual(context['nutrition_totals']['protein']['remaining'], D(0))

    def test_no_target_does_not_invent_remaining_or_progress(self):
        self.food(kcal_100g='', protein_100g=0)
        response, context = self.dashboard()
        self.assertIsNone(context['nutrition_totals']['kcal']['total'])
        for value in context['nutrition_totals'].values():
            self.assertIsNone(value['target'])
            self.assertIsNone(value['remaining'])
        self.assertIn(b'Target not set', response.data)
        self.assertIn(b'Set a target', response.data)
        self.assertIn(b'Unknown', response.data)
        self.assertNotIn(b'macro-track', response.data)

    def test_dashboard_diary_and_log_food_share_user_timezone_date_boundary(self):
        self.food(kcal_100g=100)
        self.food(day='2026-06-10', kcal_100g=500)
        response, context = self.dashboard()
        self.assertEqual(context['today'], date(2026, 6, 11))
        self.assertEqual(context['nutrition_totals']['kcal']['total'], D(100))
        self.assertIn(b'/nutrition/diary/2026-06-11/entries/new', response.data)
        self.settings(timezone='America/Los_Angeles')
        response, context = self.dashboard()
        self.assertEqual(context['today'], date(2026, 6, 10))
        self.assertEqual(context['nutrition_totals']['kcal']['total'], D(500))
        self.assertIn(b'/nutrition/diary/2026-06-10/entries/new', response.data)

    def test_today_saved_or_draft_meal_plan_uses_owned_real_link(self):
        with self.connect_db() as conn:
            plan = conn.execute("INSERT INTO meal_plan(user_id,planned_on,targets,restrictions) VALUES (%s,%s,'{}','{}') RETURNING plan_id",(self.owner,DAY)).fetchone()[0]
        response, context = self.dashboard()
        self.assertEqual(context['meal_plan']['plan_id'], plan)
        self.assertIn(b'Meal plan draft', response.data)
        self.assertIn(f'href="/nutrition/meal-plans/{plan}"'.encode(), response.data)
        with self.connect_db() as conn:
            conn.execute("UPDATE meal_plan SET status='saved',saved_at=now() WHERE plan_id=%s",(plan,))
        self.assertIn(b'Saved meal plan', self.dashboard()[0].data)
        self.settings(timezone='America/Los_Angeles')
        self.assertIsNone(self.dashboard()[1]['meal_plan'])

    def test_sidebar_and_mobile_navigation_keep_admin_server_controlled(self):
        paths = ['/dashboard','/plans','/workouts','/exercises','/nutrition','/bodyweight','/profile']
        for path in paths:
            response = self.a.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn(b'aria-label="Mobile navigation"', response.data)
            self.assertIn(b'<summary aria-controls="mobile-navigation-links">', response.data)
            self.assertNotIn(b'href="/admin/exercises"', response.data)
            self.assertNotIn(b'href="/nutrition/library/admin"', response.data)
        with self.connect_db() as conn:
            conn.execute('UPDATE app_user SET is_admin=true WHERE user_id=%s',(self.owner,))
        response, _ = self.dashboard()
        self.assertIn(b'href="/admin/exercises"', response.data)
        self.assertIn(b'href="/nutrition/library/admin"', response.data)
        self.assertNotIn(b'href="/admin/exercises"', self.b.get('/dashboard?is_admin=true').data)
        self.assertEqual(self.b.get('/admin/exercises').status_code, 403)
        self.assertEqual(self.b.get('/nutrition/library/admin').status_code, 403)
        self.assertIn(b'href="#main-content"', response.data)
        self.assertIn(b'aria-hidden="true" focusable="false"', response.data)
        self.assertNotIn(b'iconify', response.data)
        self.assertNotIn(b'tailwind', response.data)

    def test_training_preview_is_bounded_and_retains_timed_and_inactive_labels(self):
        plan = self.create_plan(); day = self.create_day(plan)
        for _ in range(7):
            self.create_entry(plan, day)
        response, context = self.dashboard()
        self.assertEqual(context['planned_sets'], 28)
        self.assertIn(b'+ 2 more exercises', response.data)
        with self.connect_db() as conn:
            timed = conn.execute("SELECT exercise_id FROM exercise WHERE name='Plank'").fetchone()[0]
            conn.execute('UPDATE plan_exercise SET exercise_id=%s,reps_min=30,reps_max=60 WHERE day_id=%s AND position=1',(timed,day))
            conn.execute('UPDATE exercise SET is_active=false WHERE exercise_id=%s',(self.exercise,))
        try:
            response, _ = self.dashboard()
            self.assertIn(b'Timed targets unavailable', response.data)
            self.assertIn(b'Inactive', response.data)
            self.assertNotIn('4 × 30'.encode(), response.data)
        finally:
            with self.connect_db() as conn:
                conn.execute('UPDATE exercise SET is_active=true WHERE exercise_id=%s',(self.exercise,))
