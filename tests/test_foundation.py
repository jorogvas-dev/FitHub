import unittest
from unittest.mock import MagicMock, patch
from werkzeug.security import check_password_hash, generate_password_hash
from psycopg.errors import UniqueViolation
from app import create_app

class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app({'TESTING': True, 'SECRET_KEY':'test-only'}).test_client()

    def token(self):
        with self.client.session_transaction() as s:
            s['csrf_token']='valid'
        return 'valid'

    def db(self,target,rows):
        patcher=patch(target)
        conn=patcher.start().return_value.__enter__.return_value
        self.addCleanup(patcher.stop)
        results=[]
        for row in rows:
            cursor=MagicMock()
            cursor.fetchone.return_value=row
            cursor.fetchall.return_value=row if isinstance(row,list) else []
            results.append(cursor)
        conn.execute.side_effect=results
        return conn

    def registration(self,**overrides):
        data=dict(csrf_token=self.token(),username='Athlete',email='ATHLETE@example.com',
                  password='secure-training-123',confirm_password='secure-training-123')
        data.update(overrides)
        return self.client.post('/register',data=data)

    def test_public_pages_and_dashboard_protection(self):
        for path in ['/','/login','/register','/static/css/style.css']:
            with self.client.get(path) as response:
                self.assertEqual(response.status_code,200)
        self.assertTrue(self.client.get('/dashboard').location.endswith('/login'))

    def test_csrf_required_and_logout_is_post_only(self):
        self.assertEqual(self.client.post('/register').status_code,400)
        self.token()
        self.assertEqual(self.client.post('/logout',data={'csrf_token':'невалиден'}).status_code,400)
        self.assertEqual(self.client.get('/logout').status_code,405)

    def test_registration_hashes_password_and_ignores_admin_flag(self):
        conn=self.db('app.routes.auth.connect',[(7,),None])
        self.assertEqual(self.registration(is_admin='true').status_code,302)
        sql,params=conn.execute.call_args_list[0].args
        self.assertEqual(params[:2],('athlete','athlete@example.com'))
        self.assertTrue(check_password_hash(params[2],'secure-training-123'))
        self.assertNotIn('is_admin',sql)
        self.assertEqual(conn.execute.call_args_list[1].args[1],(7,))
        with self.client.session_transaction() as s:
            self.assertEqual(s['user_id'],7)
            self.assertNotIn('csrf_token',s)

    def test_registration_validation_precedes_db_write(self):
        with patch('app.routes.auth.connect') as conn:
            for data in [dict(username='!'),dict(email='bad'),dict(password='short'),dict(confirm_password='wrong')]:
                self.assertEqual(self.registration(**data).status_code,200)
            conn.assert_not_called()

    def test_duplicate_registration(self):
        with patch('app.routes.auth.connect') as factory:
            factory.return_value.__enter__.return_value.execute.side_effect=UniqueViolation('duplicate')
            self.assertIn(b'already registered',self.registration().data)

    def test_login_and_logout(self):
        self.db('app.routes.auth.connect',[(9,generate_password_hash('secure-training-123')),(9,'athlete',False)])
        response=self.client.post('/login',data=dict(csrf_token=self.token(),username='Athlete',password='secure-training-123'))
        self.assertEqual(response.status_code,302)
        with self.client.session_transaction() as s:
            self.assertEqual(s['user_id'],9)
        self.assertEqual(self.client.post('/logout',data={'csrf_token':self.token()}).status_code,302)
        with self.client.session_transaction() as s:
            self.assertEqual(dict(s),{})

    def test_wrong_password(self):
        self.db('app.routes.auth.connect',[(9,generate_password_hash('secure-training-123'))])
        response=self.client.post('/login',data=dict(csrf_token=self.token(),username='athlete',password='wrong'))
        self.assertIn(b'Invalid username or password',response.data)
        with self.client.session_transaction() as s:
            self.assertNotIn('user_id',s)

    def test_dashboard_scopes_all_queries_and_escapes_username(self):
        with self.client.session_transaction() as s:
            s['user_id']=42
        self.db('app.routes.auth.connect',[(42,'<script>bad</script>',False)])
        conn=self.db('app.routes.main.connect',[(2,),(5,),[],None])
        settings=dict(timezone='Europe/Sofia',target_kcal=None,target_protein=None,target_carbs=None,target_fat=None)
        with patch('app.routes.main.settings_row',return_value=settings), patch('app.routes.main.query',return_value=[]):
            response=self.client.get('/dashboard')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'&lt;script&gt;',response.data)
        self.assertNotIn(b'<script>bad',response.data)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        for call in conn.execute.call_args_list:
            self.assertEqual(call.args[1],(42,))
            self.assertIn('WHERE',call.args[0])

    def test_exercise_search_uses_parameters(self):
        with patch('app.routes.main.connect') as factory:
            conn=factory.return_value.__enter__.return_value
            conn.execute.return_value.fetchall.return_value=[('Pull-up','Back','Bar','Control the movement','reps')]
            response=self.client.get('/exercises?q=Pull')
            self.assertEqual(response.status_code,200)
            self.assertIn(b'Pull-up',response.data)
            self.assertEqual(conn.execute.call_args.args[1],('%Pull%',)*3)

    def test_health(self):
        for status,code in [({'postgresql':'ok','chromadb':'ok'},200),({'postgresql':'unavailable','chromadb':'ok'},503)]:
            with patch('app.dependency_status',return_value=status):
                self.assertEqual(self.client.get('/health').status_code,code)

if __name__=='__main__':
    unittest.main()
