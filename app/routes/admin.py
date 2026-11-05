"""Administrator-only exercise maintenance. Referenced exercises are never deleted."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from psycopg.errors import UniqueViolation

from app.db import connect
from .auth import admin_required
from .plans import query

bp = Blueprint('admin', __name__, url_prefix='/admin/exercises')


def exercise_row(conn, exercise_id, lock=False):
    row = query(conn, 'SELECT * FROM exercise WHERE exercise_id=%s' + (' FOR UPDATE' if lock else ''),
                (exercise_id,), one=True)
    if not row:
        abort(404)
    return row


def exercise_input():
    values = []
    for field, label, limit in [('name', 'Name', 100), ('muscle_group', 'Muscle group', 40),
                               ('equipment', 'Equipment', 40), ('description', 'Instructions', 2000)]:
        value = request.form.get(field, '').strip()
        if not 1 <= len(value) <= limit:
            raise ValueError(f'{label} must contain 1–{limit} characters.')
        values.append(value)
    tracking_type = request.form.get('tracking_type', '')
    if tracking_type not in ('reps', 'duration'):
        raise ValueError('Choose repetitions or duration as the tracking type.')
    return (*values, tracking_type)


@bp.get('')
@admin_required
def index():
    search = request.args.get('q', '').strip()[:100]
    with connect() as conn:
        exercises = query(conn, 'SELECT * FROM exercise WHERE name ILIKE %s OR muscle_group ILIKE %s '
                         'OR equipment ILIKE %s ORDER BY name', ('%'+search+'%',)*3)
    return render_template('admin/exercises.html', exercises=exercises, query=search)


@bp.route('/new', methods=['GET', 'POST'])
@bp.route('/<int:exercise_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit(exercise_id=None):
    with connect() as conn:
        exercise = exercise_row(conn, exercise_id, lock=request.method == 'POST') if exercise_id is not None else None
        if request.method == 'POST':
            try:
                values = exercise_input()
            except ValueError as exc:
                flash(str(exc))
            else:
                try:
                    with conn.transaction():
                        if exercise is None:
                            conn.execute('INSERT INTO exercise(name,muscle_group,equipment,description,tracking_type) '
                                         'VALUES (%s,%s,%s,%s,%s)', values)
                        else:
                            conn.execute('UPDATE exercise SET name=%s,muscle_group=%s,equipment=%s,description=%s,'
                                         'tracking_type=%s WHERE exercise_id=%s', (*values, exercise_id))
                except UniqueViolation:
                    flash('That exercise name is already in the library. Choose another name.')
                else:
                    flash('Exercise saved.')
                    return redirect(url_for('admin.index'))
    return render_template('admin/exercise_form.html', exercise=exercise)


@bp.route('/<int:exercise_id>/deactivate', methods=['GET', 'POST'])
@admin_required
def deactivate(exercise_id):
    with connect() as conn:
        exercise = exercise_row(conn, exercise_id, lock=request.method == 'POST')
        if request.method == 'POST':
            if request.form.get('confirm') != 'deactivate':
                abort(400)
            conn.execute('UPDATE exercise SET is_active=false WHERE exercise_id=%s', (exercise_id,))
            flash('Exercise deactivated. Existing plans and history are retained.')
            return redirect(url_for('admin.index'))
    return render_template('admin/deactivate.html', exercise=exercise)


@bp.post('/<int:exercise_id>/activate')
@admin_required
def activate(exercise_id):
    with connect() as conn:
        exercise_row(conn, exercise_id, lock=True)
        conn.execute('UPDATE exercise SET is_active=true WHERE exercise_id=%s', (exercise_id,))
    flash('Exercise activated.')
    return redirect(url_for('admin.index'))
