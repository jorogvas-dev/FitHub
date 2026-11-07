import re
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from psycopg.rows import dict_row
from app.db import connect
from .auth import login_required

bp = Blueprint('plans', __name__, url_prefix='/plans')


def query(conn, sql, params=(), one=False):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone() if one else cur.fetchall()


def usable_exercise(conn, exercise_id, retained_ids=()):
    # SHARE conflicts with an admin status/type update, closing the picker/submit race.
    exercise = query(conn, 'SELECT * FROM exercise WHERE exercise_id=%s FOR SHARE', (exercise_id,), one=True)
    if not exercise:
        raise ValueError('Choose an exercise from the library.')
    if exercise['tracking_type'] != 'reps':
        raise ValueError('Timed exercises need duration targets and logging, which are not supported in this phase. Do not enter seconds as reps.')
    if not exercise['is_active'] and exercise_id not in retained_ids:
        raise ValueError('That exercise is deactivated. Choose an active exercise from the library.')
    return exercise


def owned_plan(conn, plan_id, lock=False):
    plan = query(conn, 'SELECT * FROM workout_plan WHERE plan_id=%s AND owner_id=%s' +
                 (' FOR UPDATE' if lock else ''), (plan_id, g.user['user_id']), one=True)
    if not plan:
        abort(404)
    return plan


def owned_day(conn, plan_id, day_id):
    day = query(conn, 'SELECT * FROM workout_day WHERE day_id=%s AND plan_id=%s',
                (day_id, plan_id), one=True)
    if not day:
        abort(404)
    return day


def prescription(conn, day_id, entry_id):
    entry = query(conn, 'SELECT pe.*, e.name,e.is_active,e.tracking_type FROM plan_exercise pe JOIN exercise e USING(exercise_id) WHERE pe.day_id=%s AND pe.plan_exercise_id=%s',
                  (day_id, entry_id), one=True)
    if not entry:
        abort(404)
    return entry


def name_input():
    value = request.form.get('name', '').strip()
    if not 1 <= len(value) <= 100:
        raise ValueError('Name must contain 1–100 characters.')
    return value


def integer_input(field, label, low, high):
    value = request.form.get(field, '').strip()
    if len(value) > max(10, len(str(high))) or not re.fullmatch(r'[0-9]+', value):
        raise ValueError(f'{label} must be a whole number between {low} and {high}.')
    result = int(value)
    if not low <= result <= high:
        raise ValueError(f'{label} must be between {low} and {high}.')
    return result


def entry_input():
    exercise_id = integer_input('exercise_id', 'Exercise', 1, 2147483647)
    sets = integer_input('target_sets', 'Sets', 1, 30)
    low = integer_input('reps_min', 'Minimum reps', 1, 1000)
    high = integer_input('reps_max', 'Maximum reps', low, 1000)
    return exercise_id, sets, low, high


@bp.get('')
@login_required
def index():
    with connect() as conn:
        plans = query(conn, 'SELECT p.*, (SELECT count(*) FROM workout_day d WHERE d.plan_id=p.plan_id) AS days FROM workout_plan p WHERE p.owner_id=%s ORDER BY p.created_at DESC,p.plan_id DESC', (g.user['user_id'],))
        drafts = query(conn, 'SELECT draft_id,document,created_at FROM workout_draft WHERE user_id=%s '
                       'AND saved_at IS NULL ORDER BY created_at DESC LIMIT 20', (g.user['user_id'],))
    return render_template('plans/index.html', plans=plans, drafts=drafts)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def create():
    if request.method == 'POST':
        try:
            name = name_input()
        except ValueError as exc:
            flash(str(exc))
        else:
            with connect() as conn:
                plan = query(conn, 'INSERT INTO workout_plan(owner_id,name) VALUES (%s,%s) RETURNING plan_id',
                             (g.user['user_id'], name), one=True)
            flash('Workout plan created. Add your first training day.')
            return redirect(url_for('plans.detail', plan_id=plan['plan_id']))
    return render_template('plans/name_form.html', heading='Create a workout plan', name='', back=url_for('plans.index'))


@bp.get('/<int:plan_id>')
@login_required
def detail(plan_id):
    with connect() as conn:
        plan = owned_plan(conn, plan_id)
        days = query(conn, 'SELECT * FROM workout_day WHERE plan_id=%s ORDER BY position', (plan_id,))
        entries = query(conn, 'SELECT pe.*,e.name,e.muscle_group,e.is_active,e.tracking_type FROM plan_exercise pe JOIN exercise e USING(exercise_id) JOIN workout_day d USING(day_id) WHERE d.plan_id=%s ORDER BY d.position,pe.position', (plan_id,))
    grouped = {d['day_id']: [] for d in days}
    for entry in entries:
        grouped[entry['day_id']].append(entry)
    return render_template('plans/detail.html', plan=plan, days=days, grouped=grouped)


@bp.route('/<int:plan_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(plan_id):
    with connect() as conn:
        plan = owned_plan(conn, plan_id, lock=request.method == 'POST')
        if request.method == 'POST':
            try:
                name = name_input()
            except ValueError as exc:
                flash(str(exc))
            else:
                conn.execute('UPDATE workout_plan SET name=%s WHERE plan_id=%s', (name, plan_id))
                return redirect(url_for('plans.detail', plan_id=plan_id))
    return render_template('plans/name_form.html', heading='Rename workout plan', name=plan['name'], back=url_for('plans.detail', plan_id=plan_id))


@bp.route('/<int:plan_id>/delete', methods=['GET', 'POST'])
@login_required
def delete(plan_id):
    with connect() as conn:
        plan = owned_plan(conn, plan_id, lock=request.method == 'POST')
        if request.method == 'POST':
            if request.form.get('confirm') != 'delete':
                abort(400)
            conn.execute('DELETE FROM workout_plan WHERE plan_id=%s', (plan_id,))
            flash('Workout plan deleted.')
            return redirect(url_for('plans.index'))
    return render_template('plans/delete.html', name=plan['name'], kind='workout plan', back=url_for('plans.detail', plan_id=plan_id))


@bp.route('/<int:plan_id>/days/new', methods=['GET', 'POST'])
@login_required
def day_create(plan_id):
    with connect() as conn:
        plan = owned_plan(conn, plan_id, lock=request.method == 'POST')
        if request.method == 'POST':
            try:
                name = name_input()
            except ValueError as exc:
                flash(str(exc))
            else:
                conn.execute('INSERT INTO workout_day(plan_id,name,position) SELECT %s,%s,coalesce(max(position),0)+1 FROM workout_day WHERE plan_id=%s', (plan_id, name, plan_id))
                return redirect(url_for('plans.detail', plan_id=plan_id))
    return render_template('plans/name_form.html', heading='Add a training day', name='', back=url_for('plans.detail', plan_id=plan_id))


@bp.route('/<int:plan_id>/days/<int:day_id>/edit', methods=['GET', 'POST'])
@login_required
def day_edit(plan_id, day_id):
    with connect() as conn:
        owned_plan(conn, plan_id, lock=request.method == 'POST')
        day = owned_day(conn, plan_id, day_id)
        if request.method == 'POST':
            try:
                name = name_input()
            except ValueError as exc:
                flash(str(exc))
            else:
                conn.execute('UPDATE workout_day SET name=%s WHERE day_id=%s', (name, day_id))
                return redirect(url_for('plans.detail', plan_id=plan_id))
    return render_template('plans/name_form.html', heading='Rename training day', name=day['name'], back=url_for('plans.detail', plan_id=plan_id))


@bp.route('/<int:plan_id>/days/<int:day_id>/delete', methods=['GET', 'POST'])
@login_required
def day_delete(plan_id, day_id):
    with connect() as conn:
        owned_plan(conn, plan_id, lock=request.method == 'POST')
        day = owned_day(conn, plan_id, day_id)
        if request.method == 'POST':
            if request.form.get('confirm') != 'delete':
                abort(400)
            conn.execute('DELETE FROM workout_day WHERE day_id=%s', (day_id,))
            flash('Training day deleted.')
            return redirect(url_for('plans.detail', plan_id=plan_id))
    return render_template('plans/delete.html', name=day['name'], kind='training day', back=url_for('plans.detail', plan_id=plan_id))


@bp.route('/<int:plan_id>/days/<int:day_id>/exercises/new', methods=['GET', 'POST'])
@bp.route('/<int:plan_id>/days/<int:day_id>/exercises/<int:entry_id>/edit', methods=['GET', 'POST'])
@login_required
def entry_edit(plan_id, day_id, entry_id=None):
    with connect() as conn:
        owned_plan(conn, plan_id, lock=request.method == 'POST')
        day = owned_day(conn, plan_id, day_id)
        entry = prescription(conn, day_id, entry_id) if entry_id is not None else None
        exercises = query(conn, "SELECT exercise_id,name,muscle_group,is_active FROM exercise WHERE tracking_type='reps' "
                          'AND (is_active OR exercise_id=%s) ORDER BY name', (entry['exercise_id'] if entry else None,))
        if request.method == 'POST':
            try:
                exercise_id, sets, low, high = entry_input()
                rest=integer_input('rest_seconds','Rest duration',1,3600) if request.form.get('rest_seconds','').strip() else None
                usable_exercise(conn, exercise_id, (entry['exercise_id'],) if entry else ())
            except ValueError as exc:
                flash(str(exc))
            else:
                if entry_id is None:
                    conn.execute('INSERT INTO plan_exercise(day_id,exercise_id,target_sets,reps_min,reps_max,rest_seconds,position) SELECT %s,%s,%s,%s,%s,%s,coalesce(max(position),0)+1 FROM plan_exercise WHERE day_id=%s',
                                 (day_id, exercise_id, sets, low, high, rest, day_id))
                else:
                    conn.execute('UPDATE plan_exercise SET exercise_id=%s,target_sets=%s,reps_min=%s,reps_max=%s,rest_seconds=%s WHERE plan_exercise_id=%s',
                                 (exercise_id, sets, low, high, rest, entry_id))
                return redirect(url_for('plans.detail', plan_id=plan_id))
    return render_template('plans/exercise_form.html', plan_id=plan_id, day=day, exercises=exercises, entry=entry)


@bp.route('/<int:plan_id>/days/<int:day_id>/exercises/<int:entry_id>/delete', methods=['GET', 'POST'])
@login_required
def entry_delete(plan_id, day_id, entry_id):
    with connect() as conn:
        owned_plan(conn, plan_id, lock=request.method == 'POST')
        owned_day(conn, plan_id, day_id)
        entry = prescription(conn, day_id, entry_id)
        if request.method == 'POST':
            if request.form.get('confirm') != 'delete':
                abort(400)
            conn.execute('DELETE FROM plan_exercise WHERE plan_exercise_id=%s', (entry_id,))
            return redirect(url_for('plans.detail', plan_id=plan_id))
    return render_template('plans/delete.html', name=entry['name'], kind='planned exercise', back=url_for('plans.detail', plan_id=plan_id))
