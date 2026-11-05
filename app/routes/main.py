from flask import Blueprint, g, render_template, request
from app.db import connect
from .auth import login_required

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
        measurements = conn.execute('SELECT weight_kg, measured_on FROM body_measurement WHERE user_id=%s ORDER BY measured_on DESC LIMIT 2', (g.user['user_id'],)).fetchall()
        profile = conn.execute('SELECT height_cm,goal,experience FROM fitness_profile WHERE user_id=%s', (g.user['user_id'],)).fetchone()
    latest = measurements[0] if measurements else None
    previous = measurements[1] if len(measurements) > 1 else None
    change = latest[0] - previous[0] if previous else None
    return render_template('dashboard.html', plans=plans, workouts=workouts, measurement=latest,
                           previous_measurement=previous, weight_change=change,
                           setup_required=not (profile and all(value is not None for value in profile) and latest))


@bp.get('/exercises')
def exercises():
    query = request.args.get('q', '').strip()[:100]
    with connect() as conn:
        rows = conn.execute('SELECT name,muscle_group,equipment,description,tracking_type FROM exercise '
                            'WHERE is_active AND (name ILIKE %s OR muscle_group ILIKE %s OR equipment ILIKE %s) ORDER BY name', ('%'+query+'%',)*3).fetchall()
    return render_template('exercises.html', exercises=rows, query=query)
