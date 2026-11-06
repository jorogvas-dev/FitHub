from flask import Blueprint, g, render_template, request
from app.db import connect
from app.services.nutrition import NUTRIENTS, daily_totals, target_comparison, user_today
from .auth import login_required
from .nutrition import settings_row
from .plans import query

bp = Blueprint('main', __name__)


@bp.get('/')
def index():
    return render_template('index.html')


@bp.get('/dashboard')
@login_required
def dashboard():
    with connect() as conn:
        plans = conn.execute('SELECT count(*) FROM workout_plan WHERE owner_id=%s', (g.user['user_id'],)).fetchone()[0]
        workouts = conn.execute('SELECT count(*) FROM workout_session WHERE user_id=%s', (g.user['user_id'],)).fetchone()[0]
        measurements = conn.execute('SELECT weight_kg, measured_on FROM body_measurement WHERE user_id=%s ORDER BY measured_on DESC LIMIT 4', (g.user['user_id'],)).fetchall()
        profile = conn.execute('SELECT height_cm,goal,experience FROM fitness_profile WHERE user_id=%s', (g.user['user_id'],)).fetchone()
        settings = settings_row(conn)
        today = user_today(settings['timezone'])
        entries = query(conn, 'SELECT grams,kcal_100g,protein_100g,carbs_100g,fat_100g '
                        'FROM nutrition_diary_entry WHERE user_id=%s AND logged_on=%s',
                        (g.user['user_id'], today))
        # A preview is not a scheduled workout or a request to start a session.
        training_day = query(conn, 'SELECT d.day_id,d.name AS day_name,p.plan_id,p.name AS plan_name '
                             'FROM workout_day d JOIN workout_plan p USING(plan_id) WHERE p.owner_id=%s '
                             'ORDER BY p.created_at DESC,p.plan_id DESC,d.position,d.day_id LIMIT 1',
                             (g.user['user_id'],), one=True)
        prescriptions = query(conn, 'SELECT e.name,e.tracking_type,e.is_active,pe.target_sets,pe.reps_min,pe.reps_max '
                              'FROM plan_exercise pe JOIN exercise e USING(exercise_id) '
                              'JOIN workout_day d USING(day_id) JOIN workout_plan p USING(plan_id) '
                              'WHERE pe.day_id=%s AND p.owner_id=%s ORDER BY pe.position,pe.plan_exercise_id',
                              (training_day['day_id'], g.user['user_id'])) if training_day else []
        meal_plan = query(conn, 'SELECT plan_id,status FROM meal_plan WHERE user_id=%s AND planned_on=%s '
                          "ORDER BY (status='saved') DESC,created_at DESC,plan_id DESC LIMIT 1",
                          (g.user['user_id'], today), one=True)
    latest = measurements[0] if measurements else None
    previous = measurements[1] if len(measurements) > 1 else None
    change = latest[0] - previous[0] if previous else None
    return render_template('dashboard.html', plans=plans, workouts=workouts, measurement=latest,
                           previous_measurement=previous, weight_change=change,
                           setup_required=not (profile and all(value is not None for value in profile) and latest),
                           recent_measurements=list(reversed(measurements)), settings=settings, today=today,
                           nutrition_totals=daily_totals(entries, settings), nutrients=NUTRIENTS,
                           comparison=target_comparison(settings), food_count=len(entries),
                           incomplete_entries=sum(any(entry[key+'_100g'] is None for key, _, _ in NUTRIENTS)
                                                  for entry in entries),
                           training_day=training_day, prescriptions=prescriptions,
                           planned_sets=sum(row['target_sets'] for row in prescriptions), meal_plan=meal_plan)


@bp.get('/exercises')
def exercises():
    query = request.args.get('q', '').strip()[:100]
    with connect() as conn:
        rows = conn.execute('SELECT name,muscle_group,equipment,description,tracking_type FROM exercise '
                            'WHERE is_active AND (name ILIKE %s OR muscle_group ILIKE %s OR equipment ILIKE %s) ORDER BY name', ('%'+query+'%',)*3).fetchall()
    return render_template('exercises.html', exercises=rows, query=query)
