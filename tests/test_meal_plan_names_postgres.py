"""Optional owned metadata; naming never changes meals, targets or diary snapshots."""
from app.cli import upgrade
from test_plans_postgres import PostgresTestCase


class MealPlanNameTests(PostgresTestCase):
    def draft(self,name=None):
        data=dict(planned_on='2026-11-07',include_starters='yes')
        if name is not None:data['name']=name
        response=self.post(self.a,'/nutrition/meal-plans/new',**data)
        self.assertEqual(response.status_code,302)
        return response.location

    def test_optional_name_generation_rename_and_clear(self):
        path=self.draft()
        self.assertIn(b'Meal plan #',self.a.get(path).data)
        self.assertEqual(self.post(self.a,path+'/name',name='  Weekday protein  ').status_code,302)
        self.assertIn(b'<h1>Weekday protein</h1>',self.a.get(path).data)
        self.assertIn(b'Weekday protein',self.a.get('/nutrition/meal-plans').data)
        self.assertEqual(self.post(self.a,path+'/name',name='  ').status_code,302)
        self.assertIn(b'Meal plan #',self.a.get(path).data)
        named=self.draft('Meal prep Sunday')
        self.assertIn(b'<h1>Meal prep Sunday</h1>',self.a.get(named).data)

    def test_saved_plan_rename_changes_only_name_and_preserves_history(self):
        path=self.draft('Before');identifier=int(path.rsplit('/',1)[1])
        self.assertEqual(self.post(self.a,path+'/save').status_code,302)
        with self.connect_db() as conn:
            meal=conn.execute('SELECT meal_id FROM meal_plan_meal WHERE plan_id=%s ORDER BY meal_id LIMIT 1',(identifier,)).fetchone()[0]
        self.assertEqual(self.post(self.a,path+f'/meals/{meal}/log',confirm='log').status_code,302)
        with self.connect_db() as conn:
            plan_before=conn.execute('SELECT user_id,planned_on,status,targets,restrictions,saved_at FROM meal_plan WHERE plan_id=%s',(identifier,)).fetchone()
            meals_before=conn.execute('SELECT * FROM meal_plan_meal WHERE plan_id=%s ORDER BY meal_id',(identifier,)).fetchall()
            diary_before=conn.execute('SELECT * FROM nutrition_diary_entry ORDER BY entry_id').fetchall()
        self.assertEqual(self.post(self.a,path+'/name',name='After').status_code,302)
        with self.connect_db() as conn:
            self.assertEqual(conn.execute('SELECT user_id,planned_on,status,targets,restrictions,saved_at FROM meal_plan WHERE plan_id=%s',(identifier,)).fetchone(),plan_before)
            self.assertEqual(conn.execute('SELECT * FROM meal_plan_meal WHERE plan_id=%s ORDER BY meal_id',(identifier,)).fetchall(),meals_before)
            self.assertEqual(conn.execute('SELECT * FROM nutrition_diary_entry ORDER BY entry_id').fetchall(),diary_before)

    def test_ownership_csrf_validation_and_escaped_display(self):
        path=self.draft('Original')
        self.assertEqual(self.b.get(path+'/name').status_code,404)
        self.assertEqual(self.post(self.b,path+'/name',name='Foreign').status_code,404)
        self.assertEqual(self.a.post(path+'/name',data=dict(name='No token')).status_code,400)
        for name in ('x'*101,'Two\nlines','Bad\x00name'):
            self.assertEqual(self.post(self.a,path+'/name',name=name).status_code,200)
            self.assertIn(b'<h1>Original</h1>',self.a.get(path).data)
        self.post(self.a,path+'/name',name='<script>alert(1)</script>')
        page=self.a.get(path).data
        self.assertIn(b'&lt;script&gt;',page)
        self.assertNotIn(b'<script>alert(1)</script>',page)
        response=self.post(self.a,'/nutrition/meal-plans/new',planned_on='2026-11-07',name='x'*101)
        self.assertEqual(response.status_code,200)

    def test_additive_migration_preserves_existing_plans_and_repeats(self):
        path=self.draft();identifier=int(path.rsplit('/',1)[1])
        with self.connect_db() as conn:
            before=conn.execute('SELECT plan_id,targets,restrictions FROM meal_plan WHERE plan_id=%s',(identifier,)).fetchone()
            conn.execute('ALTER TABLE meal_plan DROP COLUMN name')
            conn.execute("DELETE FROM schema_migration WHERE version='013_meal_plan_names'")
            self.assertEqual(upgrade(conn),['013_meal_plan_names'])
            self.assertEqual(upgrade(conn),[])
            self.assertEqual(conn.execute('SELECT plan_id,targets,restrictions FROM meal_plan WHERE plan_id=%s',(identifier,)).fetchone(),before)
            self.assertIsNone(conn.execute('SELECT name FROM meal_plan WHERE plan_id=%s',(identifier,)).fetchone()[0])
