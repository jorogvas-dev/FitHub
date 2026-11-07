"""Real HTTP smoke with fictional data; no inference mocks or persistent accounts.

Runs inside a web container: python scripts/smoke_submission.py --base-url https://nginx
TLS verification is disabled only for the documented local self-signed proxy.
"""
import argparse
from datetime import date
import json
import re
import secrets
import sys
from pathlib import Path
import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.db import connect


def cleanup(username):
    if not re.fullmatch(r'qa_submission_[a-f0-9]{8}',username):raise ValueError('Not a QA account.')
    with connect() as conn:
        row=conn.execute('SELECT user_id,email FROM app_user WHERE username=%s',(username,)).fetchone()
        if not row:return
        owner,email=row
        if email!=username+'@example.invalid':raise ValueError('Not a fictional QA account.')
        for statement in (
            'DELETE FROM nutrition_diary_entry WHERE user_id=%s',
            'DELETE FROM meal_plan WHERE user_id=%s',
            'DELETE FROM workout_schedule WHERE user_id=%s',
            'DELETE FROM workout_draft WHERE user_id=%s',
            'DELETE FROM set_log WHERE session_id IN (SELECT session_id FROM workout_session WHERE user_id=%s)',
            'DELETE FROM workout_session WHERE user_id=%s',
            'DELETE FROM workout_plan WHERE owner_id=%s',
            'DELETE FROM body_measurement WHERE user_id=%s',
            'DELETE FROM coach_message WHERE user_id=%s',
            'DELETE FROM fitness_profile WHERE user_id=%s',
            'DELETE FROM app_user WHERE user_id=%s'):
            conn.execute(statement,(owner,))


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--base-url',default='https://nginx')
    args=parser.parse_args()
    username='qa_submission_'+secrets.token_hex(4);password=secrets.token_urlsafe(24)
    checks=[]
    def check(ok,label):
        if not ok:raise AssertionError(label)
        checks.append(label)
    def csrf(response):return re.search(r'name="csrf_token" value="([^"]+)"',response.text).group(1)
    with httpx.Client(base_url=args.base_url,verify=False,trust_env=False,follow_redirects=True,timeout=30) as client:
        def post(path,values,form_path=None):
            token=csrf(client.get(form_path or path))
            return client.post(path,data=dict(csrf_token=token,**values))
        try:
            check(client.get('/dashboard').url.path=='/login','anonymous routes require login')
            response=post('/register',dict(username=username,email=username+'@example.invalid',password=password,confirm_password=password))
            check(response.url.path=='/onboarding','registration onboarding')
            response=post('/onboarding',dict(height_cm='175',weight_kg='75',goal='general_fitness',experience='beginner'))
            check(response.url.path=='/onboarding/training','onboarding profile and fictional weight')
            post('/onboarding/training',dict(action='skip'))
            check('Dashboard' in client.get('/dashboard').text,'skippable training preferences')
            check(client.get('/admin/exercises').status_code==403,'member cannot access admin')
            response=post('/plans/new',dict(name='Submission sample training'))
            plan=int(response.url.path.rsplit('/',1)[1])
            post(f'/plans/{plan}/days/new',dict(name='Full body - sample data'))
            with connect() as conn:
                owner=conn.execute('SELECT user_id FROM app_user WHERE username=%s',(username,)).fetchone()[0]
                day=conn.execute('SELECT day_id FROM workout_day WHERE plan_id=%s',(plan,)).fetchone()[0]
                exercise=conn.execute("SELECT exercise_id FROM exercise WHERE name='Bench press'").fetchone()[0]
            response=post(f'/plans/{plan}/days/{day}/exercises/new',dict(exercise_id=exercise,target_sets=3,reps_min=6,reps_max=12,rest_seconds=90))
            check(response.status_code==200,'manual plan and rest prescription')
            def schedule(when):
                form=client.get('/workouts/calendar/new')
                preview=re.search(r'name="preview" value="([^"]+)"',form.text).group(1)
                return client.post('/workouts/calendar/new',data=dict(csrf_token=csrf(form),preview=preview,day_id=day,scheduled_on=when))
            response=schedule(date.today().isoformat());scheduled=int(response.url.path.rsplit('/',1)[1])
            form=client.get(response.url)
            preview=re.search(r'name="preview" value="([^"]+)"',form.text).group(1)
            response=client.post(f'/workouts/calendar/{scheduled}/start',data=dict(csrf_token=csrf(form),preview=preview))
            workout=int(response.url.path.rsplit('/',1)[1]);base=f'/workouts/{workout}'
            check(client.get(base+'/timer-state').json()==dict(active=True),'scheduled start and active timer state')
            response=post(base+'/sets/new',dict(exercise_id=exercise,set_number=1,reps=8,weight_kg='40'))
            check('rest=' in str(response.url) and '"seconds": 90' in response.text,'successful set emits prescribed timer event')
            post(base+'/finish',{},base)
            check(client.get(base+'/timer-state').json()==dict(active=False),'finish clears active timer status')
            check('Completed' in client.get('/workouts/calendar').text,'calendar completion')
            check(client.get(f'/workouts/progress?exercise_id={exercise}').status_code==200,'exercise progress and baseline record')
            response=post(f'/workouts/start/{plan}/{day}',{},f'/plans/{plan}')
            second=int(response.url.path.rsplit('/',1)[1])
            post(f'/workouts/{second}/sets/new',dict(exercise_id=exercise,set_number=1,reps=9,weight_kg='45'))
            response=post(f'/workouts/{second}/finish',{},f'/workouts/{second}')
            check('New PR' in response.text,'strict improvement PR badge')
            check('New records' in client.get('/dashboard').text,'weekly recap')
            response=post('/nutrition/meal-plans/new',dict(planned_on=date.today().isoformat(),name='Submission sample meals',include_starters='yes'))
            check('Submission sample meals' in response.text and '/meal-plans/' in response.url.path,'manual nutrition draft with unset targets and optional name')
            meal_plan=response.url.path
            form=client.get(meal_plan);preview=re.search(r'name="limits_preview" value="([^"]+)"',form.text).group(1)
            response=client.post(meal_plan+'/save',data=dict(csrf_token=csrf(form),limits_preview=preview))
            check('Swap this meal' in response.text,'explicit meal plan save')
            swap=re.search(r'href="([^"]+/swap)"',response.text).group(1)
            check(client.get(swap+'?goal=similar').status_code==200,'eligible meal swap preview')
            post('/nutrition/settings',dict(timezone='Europe/Sofia',target_kcal='100',allergies='milk',unwanted_ingredients='chicken',dietary_preferences='vegetarian'))
            check('exceeds' in client.get(meal_plan).text.lower() or 'blocked' in client.get(meal_plan).text.lower(),'current target/exclusion revalidation')
            with connect() as conn:before=conn.execute('SELECT count(*) FROM meal_plan WHERE user_id=%s',(owner,)).fetchone()[0]
            response=post('/nutrition/meal-plans/ai',dict(planned_on=date.today().isoformat()))
            check('No recipes satisfy your current exclusions' in response.text,'empty allergy-safe shortlist fails closed before inference')
            with connect() as conn:check(conn.execute('SELECT count(*) FROM meal_plan WHERE user_id=%s',(owner,)).fetchone()[0]==before,'blocked nutrition generation creates no draft')
            with connect() as conn:conn.execute('UPDATE app_user SET is_admin=true WHERE user_id=%s',(owner,))
            check(client.get('/admin/exercises').status_code==200,'explicitly promoted admin access')
            post('/logout',{},'/dashboard')
            check(client.get('/dashboard').url.path=='/login','logout protection')
            response=post('/login',dict(username=username,password=password))
            check(response.url.path=='/dashboard','login with temporary account')
            print(json.dumps(dict(mocked=False,ai_called=False,http_checks=len(checks),passed=checks,cleanup='temporary account removed in finally')))
        finally:cleanup(username)


if __name__=='__main__':main()
