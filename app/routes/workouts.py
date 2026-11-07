"""Private workout logging using the existing session and set tables."""
import json
import re
from decimal import Decimal

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for, current_app, jsonify
from itsdangerous import URLSafeTimedSerializer, BadSignature
from time import time
from psycopg.errors import UniqueViolation

from app.db import connect
from app.services import workout_progress as progress
from app.services import personal_records
from app.services import workout_review
from .auth import login_required
from .plans import integer_input, owned_day, owned_plan, query, usable_exercise

bp = Blueprint('workouts', __name__, url_prefix='/workouts')
SNAPSHOT_PREFIX = progress.SNAPSHOT_PREFIX


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
    return query(conn, 'SELECT pe.exercise_id,e.name,e.tracking_type,pe.target_sets,pe.reps_min,pe.reps_max,pe.rest_seconds '
                 'FROM plan_exercise pe JOIN exercise e USING(exercise_id) '
                 'WHERE pe.day_id=%s ORDER BY pe.position', (day_id,))


def session_plan(conn, session):
    # Older sessions may contain ordinary notes or have no remaining training day.
    snapshot=progress.read_snapshot(session['notes'])
    if snapshot is not None:
        return snapshot
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
        conn.execute('SELECT user_id FROM app_user WHERE user_id=%s FOR UPDATE',(g.user['user_id'],))
        # Plan editors also lock this row, keeping the snapshot consistent with the day.
        plan = owned_plan(conn, plan_id, lock=True)
        day = owned_day(conn, plan_id, day_id)
        session = create_session(conn,plan,day)
    return redirect(url_for('workouts.detail', session_id=session['session_id']))


def prescription_snapshot(conn,plan,day):
    return dict(plan_name=plan['name'],day_name=day['name'],exercises=planned_exercises(conn,day['day_id']))


def create_session(conn,plan,day):
    """Caller holds owner and plan locks; repeated starts resume the same day."""
    active=query(conn,'SELECT session_id FROM workout_session WHERE user_id=%s AND day_id=%s '
                 'AND finished_at IS NULL ORDER BY started_at DESC,session_id DESC LIMIT 1',
                 (g.user['user_id'],day['day_id']),one=True)
    if active:return active
    return query(conn,'INSERT INTO workout_session(user_id,day_id,notes) VALUES (%s,%s,%s) RETURNING session_id',
                 (g.user['user_id'],day['day_id'],SNAPSHOT_PREFIX+json.dumps(prescription_snapshot(conn,plan,day))),one=True)


def rest_serializer():
    return URLSafeTimedSerializer(current_app.secret_key,salt='workout-rest-v1')


def rest_seconds(plan,exercise_id,number):
    targets=[row for row in plan['exercises'] if progress.stable_id(row)==exercise_id]
    if len(targets)==1:return targets[0].get('rest_seconds')
    for target in targets:
        number-=target.get('target_sets',0)
        if number<=0:return target.get('rest_seconds')
    return None


def timer_context(workout):
    event=None
    raw=request.args.get('rest')
    if raw and workout['finished_at'] is None:
        try:
            value=rest_serializer().loads(raw,max_age=1800)
            if value['user']==g.user['user_id'] and value['workout']==workout['session_id']:event=value
        except (BadSignature,KeyError,TypeError):pass
    return dict(workout=workout['session_id'],finished=workout['finished_at'] is not None,server_ms=int(time()*1000),event=event)


@bp.get('/<int:session_id>/timer-state')
@login_required
def timer_state(session_id):
    with connect() as conn:workout=owned_session(conn,session_id)
    return jsonify(active=workout['finished_at'] is None)


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
    return render_workout(session_id)


@bp.post('/<int:session_id>/review')
@login_required
def review(session_id):
    return render_workout(session_id, generate_review=True)


def render_workout(session_id, generate_review=False):
    with connect() as conn:
        session = owned_session(conn, session_id)
        if generate_review and session['finished_at'] is None:
            abort(409, description='Finish this workout before requesting a review.')
        plan = session_plan(conn, session)
        # Join the exercise catalogue directly: logs survive plan/day deletion.
        logs = query(conn, 'SELECT l.*,e.name,e.tracking_type,e.is_active FROM set_log l JOIN exercise e USING(exercise_id) '
                     'WHERE l.session_id=%s ORDER BY e.name,l.set_number', (session_id,))
        cards=progress.display_cards(plan['exercises'],logs)
        references=progress.last_session_sets(conn,g.user['user_id'],[
            card['exercise']['exercise_id'] for card in cards if card['exercise']['exercise_id'] is not None
        ]) if session['finished_at'] is None else {}
        pr_badges=personal_records.workout_badges(conn,g.user['user_id'],session,logs) if session['finished_at'] is not None else {}
        facts=workout_review.build_facts(conn,g.user['user_id'],session,logs,pr_badges) if generate_review else None
    # Release database connections before the bounded provider call. No review is persisted.
    review=workout_review.generate_review(facts) if facts is not None else None
    return render_template('workouts/detail.html', workout=session, plan=plan, logs=logs,cards=cards,references=references,pr_badges=pr_badges,review=review,timer=timer_context(session))


@bp.get('/progress')
@login_required
def exercise_progress():
    with connect() as conn:
        exercises=progress.exercise_choices(conn,g.user['user_id'])
        selected=None
        raw=request.args.get('exercise_id')
        if raw is not None:
            if not re.fullmatch(r'[1-9][0-9]{0,18}',raw) or int(raw)>9223372036854775807:
                abort(400,description='Choose a valid exercise from your completed workout history.')
            selected=next((row for row in exercises if row['exercise_id']==int(raw)),None)
            if selected is None:
                abort(404)
        elif exercises:
            selected=exercises[0]
        points=progress.session_points(conn,g.user['user_id'],selected['exercise_id']) if selected else []
        records=personal_records.exercise_records(conn,g.user['user_id'],selected['exercise_id']) if selected else None
    return render_template('workouts/progress.html',exercises=exercises,selected=selected,points=points,chart=progress.chart_tracks(points),records=records)


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
        retained = {identifier for exercise in session_plan(conn, session)['exercises']
                    if (identifier:=progress.stable_id(exercise)) is not None}
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
                            added=conn.execute('INSERT INTO set_log(exercise_id,set_number,reps,weight_kg,rir,session_id) '
                                         'VALUES (%s,%s,%s,%s,%s,%s) RETURNING set_id', (*values, session_id)).fetchone()[0]
                        else:
                            conn.execute('UPDATE set_log SET exercise_id=%s,set_number=%s,reps=%s,weight_kg=%s,rir=%s '
                                         'WHERE session_id=%s AND set_id=%s', (*values, session_id, set_id))
                except UniqueViolation:
                    flash('That exercise already has a set with this number. Choose another set number or edit the existing set.')
                else:
                    if entry is None:
                        event=dict(user=g.user['user_id'],workout=session_id,set_id=added,started_ms=int(time()*1000),
                                   seconds=rest_seconds(session_plan(conn,session),values[0],values[1]))
                        return redirect(url_for('workouts.detail',session_id=session_id,rest=rest_serializer().dumps(event)))
                    return redirect(url_for('workouts.detail', session_id=session_id))
    return render_template('workouts/set_form.html', workout=session, entry=entry, exercises=exercises,timer=timer_context(session))


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
