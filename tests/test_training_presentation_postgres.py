"""Exercise-specific navigation, historical rendering and explicit training-day starts."""
from urllib.parse import parse_qs, urlsplit
from html.parser import HTMLParser

from test_dashboard_postgres import Links
from test_plans_postgres import PostgresTestCase


class Forms(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'form':
            self.current = dict(attrs, inputs=[], labels=[])
            self.forms.append(self.current)
        elif self.current is not None and tag == 'input':
            self.current['inputs'].append(dict(attrs))
        elif self.current is not None and tag == 'button':
            self.current['labels'].append(dict(attrs).get('aria-label', ''))

    def handle_endtag(self, tag):
        if tag == 'form':
            self.current = None


class TrainingPresentationTests(PostgresTestCase):
    def start(self):
        plan = self.create_plan('Strength block')
        day = self.create_day(plan, 'Upper A')
        self.create_entry(plan, day)
        response = self.post(self.a, f'/workouts/start/{plan}/{day}')
        self.assertEqual(response.status_code, 302)
        return plan, day, int(response.location.rsplit('/', 1)[1])

    def test_explicit_day_start_forms_and_empty_plan_do_not_start_on_get(self):
        plan = self.create_plan('Two-day block')
        days = [self.create_day(plan, name) for name in ['Upper A', 'Lower B']]
        html = self.a.get(f'/plans/{plan}').get_data(as_text=True)
        starts = [form for form in Forms(html).forms if form.get('action', '').startswith('/workouts/start/')]
        self.assertEqual([form['action'] for form in starts], [f'/workouts/start/{plan}/{day}' for day in days])
        for form, name in zip(starts, ['Upper A', 'Lower B']):
            self.assertEqual(form['method'], 'post')
            self.assertTrue(any(field.get('name') == 'csrf_token' and field.get('value') for field in form['inputs']))
            self.assertIn('Start workout: Two-day block · '+name, form['labels'])
        empty = self.create_plan('No training days')
        html = self.a.get(f'/plans/{empty}').get_data(as_text=True)
        self.assertIn('This plan has no training days yet', html)
        self.assertFalse(any(form.get('action', '').startswith('/workouts/start/') for form in Forms(html).forms))
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM workout_session').fetchone()[0], 0)

    def test_add_set_link_selects_exercise_and_next_number_and_preserves_invalid_post(self):
        _, _, session = self.start()
        base = f'/workouts/{session}'
        for number in [1, 3]:
            self.assertEqual(self.post(self.a, base+'/sets/new', exercise_id=self.exercise,
                                       set_number=number, reps=8, weight_kg='', rir=0).status_code, 302)
        html = self.a.get(base).get_data(as_text=True)
        link = next(link for link in Links(html).links if link['text'] == 'Add set')
        self.assertEqual(parse_qs(urlsplit(link['href']).query), {'exercise_id':[str(self.exercise)], 'set_number':['4']})
        form = self.a.get(link['href']).get_data(as_text=True)
        self.assertIn(f'<option value="{self.exercise}" selected>', form)
        self.assertIn('name="set_number" min="1" max="2147483647" step="1" required value="4"', form)
        invalid = self.post(self.a, link['href'], exercise_id=self.exercise, set_number=3, reps=12, weight_kg='7.50', rir=2)
        self.assertEqual(invalid.status_code, 200)
        self.assertIn(b'already has a set', invalid.data)
        self.assertIn(b'value="7.50"', invalid.data)
        self.assertIn(b'Bodyweight', self.a.get(base).data)
        self.assertIn(b'>0</dd>', self.a.get(base).data)

    def test_repeated_prescriptions_keep_both_targets_without_duplicating_logged_sets(self):
        plan, day, session = self.start()
        # Start a fresh session after adding another prescription for the same exercise.
        self.post(self.a,f'/workouts/{session}/finish')
        entry = self.create_entry(plan, day)
        self.assertEqual(self.post(self.a, f'/plans/{plan}/days/{day}/exercises/{entry}/edit',
                                   exercise_id=self.exercise, target_sets=2, reps_min=12, reps_max=15).status_code, 302)
        response = self.post(self.a, f'/workouts/start/{plan}/{day}')
        session = int(response.location.rsplit('/', 1)[1])
        self.post(self.a, f'/workouts/{session}/sets/new', exercise_id=self.exercise, set_number=1, reps=9, weight_kg=0)
        with self.connect_db() as conn:
            set_id = conn.execute('SELECT set_id FROM set_log WHERE session_id=%s', (session,)).fetchone()[0]
        html = self.a.get(f'/workouts/{session}').get_data(as_text=True)
        self.assertIn('6–10 reps', html)
        self.assertIn('12–15 reps', html)
        self.assertEqual(html.count(f'data-set-id="{set_id}"'), 1)
        self.assertEqual(len([link for link in Links(html).links if link['text'] == 'Add set']), 1)

    def test_finished_history_retains_snapshot_names_targets_after_original_plan_deleted(self):
        with self.connect_db() as conn:
            exercise = conn.execute("INSERT INTO exercise(name,muscle_group,equipment,description) "
                                    "VALUES ('Original exercise','Back','Bar','Test') RETURNING exercise_id").fetchone()[0]
        self.exercise = exercise
        plan, day, session = self.start()
        self.post(self.a, f'/workouts/{session}/sets/new', exercise_id=exercise, set_number=1, reps=8, weight_kg=12.5, rir=1)
        self.post(self.a, f'/workouts/{session}/finish')
        with self.connect_db() as conn:
            conn.execute('UPDATE exercise SET name=%s WHERE exercise_id=%s', ('Renamed catalogue exercise', exercise))
        self.post(self.a, f'/plans/{plan}/delete', confirm='delete')
        html = self.a.get(f'/workouts/{session}').get_data(as_text=True)
        for text in ['Original exercise', 'Upper A', 'Strength block', '6–10 reps', '12.50', 'read-only']:
            self.assertIn(text, html)
        self.assertNotIn('Renamed catalogue exercise', html)
        self.assertFalse(any(link['text'] == 'Add set' or '/sets/' in link['href'] for link in Links(html).links))
        workout_forms = [form for form in Forms(html).forms if form.get('action', '').startswith(f'/workouts/{session}/')]
        self.assertEqual([form['action'] for form in workout_forms], [f'/workouts/{session}/review#workout-review'])
        self.assertEqual(workout_forms[0]['method'], 'post')
        self.assertTrue(any(field.get('name') == 'csrf_token' for field in workout_forms[0]['inputs']))
        self.start()  # A separate active session distinguishes both history states.
        history = self.a.get('/workouts').data
        self.assertIn(b'In progress', history)
        self.assertIn(b'Finished', history)
        self.assertIn(b'Read-only', history)

    def test_timed_legacy_targets_and_sets_remain_visible_without_add_set_action(self):
        plan = self.create_plan()
        day = self.create_day(plan)
        with self.connect_db() as conn:
            timed = conn.execute("SELECT exercise_id FROM exercise WHERE tracking_type='duration' ORDER BY exercise_id LIMIT 1").fetchone()[0]
            conn.execute('INSERT INTO plan_exercise(day_id,exercise_id,position,target_sets,reps_min,reps_max) VALUES (%s,%s,1,2,30,45)', (day,timed))
        response = self.post(self.a, f'/workouts/start/{plan}/{day}')
        session = int(response.location.rsplit('/',1)[1])
        with self.connect_db() as conn:
            conn.execute('INSERT INTO set_log(session_id,exercise_id,set_number,reps) VALUES (%s,%s,1,30)', (session,timed))
        html = self.a.get(f'/workouts/{session}').get_data(as_text=True)
        for text in ['duration target unavailable', 'legacy values retained', 'Legacy reps (recorded)']:
            self.assertIn(text, html)
        self.assertFalse(any(link['text'] == 'Add set' for link in Links(html).links))
        self.assertNotIn('30–45 reps', html)
        picker = self.a.get(f'/workouts/{session}/sets/new?exercise_id={timed}').get_data(as_text=True)
        self.assertNotIn(f'<option value="{timed}"', picker)
        self.assertIn('Timed durations cannot be recorded as reps', picker)
