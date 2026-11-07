"""Private generated drafts. Only confirmed, revalidated saves insert workout plans."""
import uuid

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from psycopg.types.json import Jsonb

from app.db import connect
from app.services import workout_ai as inference
from app.services.workout_generation import (EQUIPMENT, MOVEMENTS, MAX_EXERCISES, candidates,
    form_integer, preferences_input, strict_json, validate_document)
from .auth import login_required
from .plans import query

bp = Blueprint('workout_ai', __name__)


def preferences_row(conn, lock=False):
    return query(conn, 'SELECT * FROM training_preferences WHERE user_id=%s'+
                 (' FOR SHARE' if lock else ''), (g.user['user_id'],), one=True)


def training_preferences(row):
    if not row or row['status'] != 'completed':
        raise ValueError('Complete your optional training preferences before generating a draft.')
    return {key: row[key] for key in ('days_per_week', 'minutes_per_session', 'equipment', 'avoid_movements')}


def profile_snapshot(conn):
    row = query(conn, 'SELECT height_cm,goal,experience FROM fitness_profile WHERE user_id=%s',
                (g.user['user_id'],), one=True) or {}
    weight = query(conn, 'SELECT weight_kg FROM body_measurement WHERE user_id=%s ORDER BY measured_on DESC LIMIT 1',
                   (g.user['user_id'],), one=True)
    if not all(row.get(field) is not None for field in ('height_cm', 'goal', 'experience')) or not weight:
        raise ValueError('Complete your fitness profile and record a bodyweight measurement first.')
    return dict(height_cm=str(row['height_cm']), weight_kg=str(weight['weight_kg']),
                goal=row['goal'], experience=row['experience'])


def catalogue_rows(conn, lock=False):
    return query(conn, 'SELECT exercise_id,name,muscle_group,equipment,is_active,tracking_type FROM exercise '
                 'ORDER BY exercise_id'+(' FOR SHARE' if lock else ''))


@bp.route('/onboarding/training', methods=['GET', 'POST'])
@login_required
def preferences():
    with connect() as conn:
        stored = preferences_row(conn)
        if request.method == 'POST':
            if request.form.get('action') == 'skip':
                conn.execute("INSERT INTO training_preferences(user_id,status) VALUES (%s,'skipped') "
                             'ON CONFLICT(user_id) DO NOTHING', (g.user['user_id'],))
                flash('Training preferences skipped. You can edit them later from your profile or Workout plans.')
                return redirect(url_for('main.dashboard'))
            try:
                values = preferences_input(request.form)
            except ValueError as exc:
                flash(str(exc))
            else:
                conn.execute('INSERT INTO training_preferences(user_id,days_per_week,minutes_per_session,equipment,'
                             "avoid_movements,status) VALUES (%s,%s,%s,%s,%s,'completed') ON CONFLICT(user_id) "
                             'DO UPDATE SET days_per_week=EXCLUDED.days_per_week,minutes_per_session=EXCLUDED.minutes_per_session,'
                             "equipment=EXCLUDED.equipment,avoid_movements=EXCLUDED.avoid_movements,status='completed',updated_at=now()",
                             (g.user['user_id'], values['days_per_week'], values['minutes_per_session'],
                              values['equipment'], values['avoid_movements']))
                flash('Training preferences saved. Generate a draft or continue with the manual builder.')
                return redirect(url_for('workout_ai.generate'))
    return render_template('plans/training_preferences.html', preferences=stored or {}, equipment=EQUIPMENT,
                           movements=MOVEMENTS)


@bp.route('/plans/generate', methods=['GET', 'POST'])
@login_required
def generate():
    error = None
    preferences = None
    profile = None
    exercises = []
    try:
        with connect() as conn:
            preferences = training_preferences(preferences_row(conn))
            profile = profile_snapshot(conn)
            exercises = candidates(catalogue_rows(conn), preferences)
        if not exercises:
            raise ValueError('No supported exercises match your equipment and movement exclusions. Adjust preferences or use the manual builder.')
        if request.method == 'POST':
            # No database connection/locks are held while waiting for the AI.
            raw = inference.generate(preferences, profile, exercises)
            document = validate_document(strict_json(raw), preferences, exercises,
                                         [row['exercise_id'] for row in exercises])
            identifier = uuid.uuid4()
            with connect() as conn:
                current_preferences = training_preferences(preferences_row(conn, lock=True))
                if current_preferences != preferences:
                    raise ValueError('Training preferences changed during generation. Generate a new draft.')
                document = validate_document(document, current_preferences, catalogue_rows(conn, lock=True),
                                             [row['exercise_id'] for row in exercises])
                conn.execute('INSERT INTO workout_draft(draft_id,user_id,document,preferences,candidate_ids) '
                             'VALUES (%s,%s,%s,%s,%s)', (identifier, g.user['user_id'], Jsonb(document),
                              Jsonb(dict(training=preferences, profile=profile)),
                              [row['exercise_id'] for row in exercises]))
            return redirect(url_for('workout_ai.preview', draft_id=identifier))
    except (ValueError, inference.InferenceError) as exc:
        error = str(exc)
    return render_template('plans/generate.html', preferences=preferences, profile=profile, error=error,
                           candidate_count=len(exercises))


def edited_document(stored, form):
    days = []
    for i, day in enumerate(stored['days']):
        rows = []
        for j in range(min(MAX_EXERCISES, len(day['exercises']) + 1)):
            prefix = f'd{i}_e{j}_'
            if form.get(prefix+'remove') == 'yes':
                continue
            identifier = form.get(prefix+'exercise_id', '')
            if not identifier and j == len(day['exercises']):
                continue
            rows.append(dict(exercise_id=form_integer(identifier, 'Exercise', 1, 2147483647),
                             sets=form_integer(form.get(prefix+'sets'), 'Sets', 1, 6),
                             reps_min=form_integer(form.get(prefix+'reps_min'), 'Minimum reps', 1, 30),
                             reps_max=form_integer(form.get(prefix+'reps_max'), 'Maximum reps', 1, 30)))
        days.append(dict(name=form.get(f'd{i}_name', ''), estimated_minutes=1, exercises=rows))
    return dict(name=form.get('name', ''), days=days)


@bp.route('/plans/drafts/<uuid:draft_id>', methods=['GET', 'POST'])
@login_required
def preview(draft_id):
    with connect() as conn:
        draft = query(conn, 'SELECT * FROM workout_draft WHERE draft_id=%s AND user_id=%s'+
                      (' FOR UPDATE' if request.method == 'POST' else ''), (draft_id, g.user['user_id']), one=True)
        if not draft:
            abort(404)
        if draft['saved_at']:
            if not draft['saved_plan_id']:
                abort(409, description='This draft was already saved; the resulting plan has since been deleted.')
            return redirect(url_for('plans.detail', plan_id=draft['saved_plan_id']))
        settings = preferences_row(conn, lock=request.method == 'POST')
        catalogue = catalogue_rows(conn, lock=request.method == 'POST')
        try:
            preferences = training_preferences(settings)
            choices = [row for row in candidates(catalogue, preferences) if row['exercise_id'] in draft['candidate_ids']]
        except ValueError:
            preferences = None
            choices = []
        error = None
        document = draft['document']
        if request.method == 'POST':
            try:
                revision = form_integer(request.form.get('revision'), 'Draft revision', 1, 2147483647)
            except ValueError as exc:
                abort(400, description=str(exc))
            if revision != draft['revision']:
                abort(409, description='This draft changed in another tab. Reload it before editing or saving.')
            action = request.form.get('action')
            if action not in ('update', 'save'):
                abort(400)
            try:
                if not preferences:
                    raise ValueError('Complete your training preferences before saving this draft.')
                profile_snapshot(conn)
                document = edited_document(draft['document'], request.form)
                document = validate_document(document, preferences, catalogue, draft['candidate_ids'])
                if action == 'save':
                    if request.form.get('confirm') != 'save':
                        raise ValueError('Confirm Save as new plan before creating a workout plan.')
                    plan = query(conn, 'INSERT INTO workout_plan(owner_id,name) VALUES (%s,%s) RETURNING plan_id',
                                 (g.user['user_id'], document['name']), one=True)
                    for position, day in enumerate(document['days'], 1):
                        saved_day = query(conn, 'INSERT INTO workout_day(plan_id,name,position) VALUES (%s,%s,%s) RETURNING day_id',
                                          (plan['plan_id'], day['name'], position), one=True)
                        for index, row in enumerate(day['exercises'], 1):
                            conn.execute('INSERT INTO plan_exercise(day_id,exercise_id,position,target_sets,reps_min,reps_max) '
                                         'VALUES (%s,%s,%s,%s,%s,%s)', (saved_day['day_id'], row['exercise_id'], index,
                                          row['sets'], row['reps_min'], row['reps_max']))
                    conn.execute('UPDATE workout_draft SET document=%s,saved_at=now(),saved_plan_id=%s WHERE draft_id=%s',
                                 (Jsonb(document), plan['plan_id'], draft_id))
                    flash('Workout plan saved. Review and edit its training days before starting a workout.')
                    return redirect(url_for('plans.detail', plan_id=plan['plan_id']))
                conn.execute('UPDATE workout_draft SET document=%s,revision=revision+1 WHERE draft_id=%s',
                             (Jsonb(document), draft_id))
                flash('Draft updated. No workout plan or log has been created.')
                return redirect(url_for('workout_ai.preview', draft_id=draft_id))
            except ValueError as exc:
                error = str(exc)
                # Keep the last valid document for display; submitted values remain in request.form.
                document = draft['document']
    return render_template('plans/draft.html', draft=draft, document=document, exercises=choices, error=error,
                           preferences=preferences)
