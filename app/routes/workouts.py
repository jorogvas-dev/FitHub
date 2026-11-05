"""Private workout logging using the existing session and set tables."""
import json
import re
from decimal import Decimal

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from psycopg.errors import UniqueViolation

from app.db import connect
from .auth import login_required
from .plans import integer_input, owned_day, owned_plan, query, usable_exercise

bp = Blueprint('workouts', __name__, url_prefix='/workouts')
SNAPSHOT_PREFIX = 'FitHub workout snapshot v1\n'


def owned_session(conn, session_id, lock=False):
    session = query(conn, 'SELECT * FROM workout_session WHERE session_id=%s AND user_id=%s' +
                    (' FOR UPDATE' if lock else ''), (session_id, g.user['user_id']), one=True)
    if not session:
        abort(404)
    return session


def writable(session):
    if session['finished_at'] is not None:
        abort(409, description='This workout is finished and read-only.')


def planned_exercises(conn, day_id):
    return query(conn, 'SELECT pe.exercise_id,e.name,e.tracking_type,pe.target_sets,pe.reps_min,pe.reps_max '
                 'FROM plan_exercise pe JOIN exercise e USING(exercise_id) '
                 'WHERE pe.day_id=%s ORDER BY pe.position', (day_id,))


def session_plan(conn, session):
    # Older sessions may contain ordinary notes or have no remaining training day.
    notes = session['notes']
    if notes.startswith(SNAPSHOT_PREFIX):
        try:
            snapshot = json.loads(notes[len(SNAPSHOT_PREFIX):])
            if (isinstance(snapshot, dict) and isinstance(snapshot.get('day_name'), str)
                    and isinstance(snapshot.get('plan_name'), str)
                    and isinstance(snapshot.get('exercises'), list)):
                return snapshot
        except (ValueError, TypeError):
            pass
    day = query(conn, 'SELECT d.name AS day_name,p.name AS plan_name FROM workout_day d '
                'JOIN workout_plan p USING(plan_id) WHERE d.day_id=%s AND p.owner_id=%s',
                (session['day_id'], g.user['user_id']), one=True)
    return dict(day, exercises=planned_exercises(conn, session['day_id'])) if day else {
        'day_name': 'Workout', 'plan_name': 'Original training day unavailable', 'exercises': []}


def set_input():
    exercise_id = integer_input('exercise_id', 'Exercise', 1, 9223372036854775807)
    number = integer_input('set_number', 'Set number', 1, 2147483647)
    reps = integer_input('reps', 'Reps', 0, 1000)
    weight = request.form.get('weight_kg', '').strip()
    if weight:
        if len(weight) > 8 or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', weight):
            raise ValueError('Weight must be between 0 and 10000 kg with at most two decimal places, or blank for bodyweight.')
        weight = Decimal(weight)
        if not 0 <= weight <= 10000:
            raise ValueError('Weight must be between 0 and 10000 kg.')
    else:
        weight = None
    rir = integer_input('rir', 'RIR', 0, 10) if request.form.get('rir', '').strip() else None
    return exercise_id, number, reps, weight, rir


@bp.post('/start/<int:plan_id>/<int:day_id>')
@login_required
def start(plan_id, day_id):
    with connect() as conn:
        # Plan editors also lock this row, keeping the snapshot consistent with the day.
        plan = owned_plan(conn, plan_id, lock=True)
        day = owned_day(conn, plan_id, day_id)
        snapshot = dict(plan_name=plan['name'], day_name=day['name'],
                        exercises=planned_exercises(conn, day_id))
        session = query(conn, 'INSERT INTO workout_session(user_id,day_id,notes) VALUES (%s,%s,%s) RETURNING session_id',
                        (g.user['user_id'], day_id, SNAPSHOT_PREFIX + json.dumps(snapshot)), one=True)
    return redirect(url_for('workouts.detail', session_id=session['session_id']))


@bp.get('')
@login_required
def history():
    with connect() as conn:
        sessions = query(conn, 'SELECT s.*,d.name AS day_name,p.name AS plan_name,'
                         '(SELECT count(*) FROM set_log l WHERE l.session_id=s.session_id) AS set_count '
                         'FROM workout_session s LEFT JOIN workout_day d USING(day_id) '
                         'LEFT JOIN workout_plan p USING(plan_id) WHERE s.user_id=%s '
                         'ORDER BY s.started_at DESC,s.session_id DESC', (g.user['user_id'],))
        for session in sessions:
            session['plan'] = session_plan(conn, session)
    return render_template('workouts/history.html', sessions=sessions)


@bp.get('/<int:session_id>')
@login_required
def detail(session_id):
    with connect() as conn:
        session = owned_session(conn, session_id)
        plan = session_plan(conn, session)
        # Join the exercise catalogue directly: logs survive plan/day deletion.
        logs = query(conn, 'SELECT l.*,e.name,e.tracking_type FROM set_log l JOIN exercise e USING(exercise_id) '
                     'WHERE l.session_id=%s ORDER BY e.name,l.set_number', (session_id,))
    return render_template('workouts/detail.html', workout=session, plan=plan, logs=logs)


@bp.route('/<int:session_id>/sets/new', methods=['GET', 'POST'])
@bp.route('/<int:session_id>/sets/<int:set_id>/edit', methods=['GET', 'POST'])
@login_required
def set_edit(session_id, set_id=None):
    with connect() as conn:
        session = owned_session(conn, session_id, lock=request.method == 'POST')
        entry = None
        if set_id is not None:
            entry = query(conn, 'SELECT * FROM set_log WHERE session_id=%s AND set_id=%s',
                          (session_id, set_id), one=True)
            if not entry:
                abort(404)
        writable(session)
        # Continue an existing session's exercises even if subsequently deactivated.
        retained = {e['exercise_id'] for e in session_plan(conn, session)['exercises']}
        retained.update(row['exercise_id'] for row in query(conn, 'SELECT DISTINCT exercise_id FROM set_log WHERE session_id=%s', (session_id,)))
        exercises = query(conn, "SELECT exercise_id,name,is_active FROM exercise WHERE tracking_type='reps' "
                          'AND (is_active OR exercise_id=ANY(%s)) ORDER BY name', (list(retained),))
        if request.method == 'POST':
            try:
                values = set_input()
                usable_exercise(conn, values[0], retained)
            except ValueError as exc:
                flash(str(exc))
            else:
                try:
                    # Savepoint restores the outer transaction (and its session lock) on duplicates.
                    with conn.transaction():
                        if entry is None:
                            conn.execute('INSERT INTO set_log(exercise_id,set_number,reps,weight_kg,rir,session_id) '
                                         'VALUES (%s,%s,%s,%s,%s,%s)', (*values, session_id))
                        else:
                            conn.execute('UPDATE set_log SET exercise_id=%s,set_number=%s,reps=%s,weight_kg=%s,rir=%s '
                                         'WHERE session_id=%s AND set_id=%s', (*values, session_id, set_id))
                except UniqueViolation:
                    flash('That exercise already has a set with this number. Choose another set number or edit the existing set.')
                else:
                    return redirect(url_for('workouts.detail', session_id=session_id))
    return render_template('workouts/set_form.html', workout=session, entry=entry, exercises=exercises)


@bp.post('/<int:session_id>/sets/<int:set_id>/delete')
@login_required
def set_delete(session_id, set_id):
    with connect() as conn:
        session = owned_session(conn, session_id, lock=True)
        writable(session)
        deleted = conn.execute('DELETE FROM set_log WHERE session_id=%s AND set_id=%s RETURNING set_id',
                               (session_id, set_id)).fetchone()
        if not deleted:
            abort(404)
    return redirect(url_for('workouts.detail', session_id=session_id))


@bp.post('/<int:session_id>/finish')
@login_required
def finish(session_id):
    with connect() as conn:
        session = owned_session(conn, session_id, lock=True)
        if session['finished_at'] is None:
            # A waiting transaction's now() may predate the session; use the wall clock.
            conn.execute('UPDATE workout_session SET finished_at=clock_timestamp() '
                         'WHERE session_id=%s AND finished_at IS NULL', (session_id,))
    return redirect(url_for('workouts.detail', session_id=session_id))
