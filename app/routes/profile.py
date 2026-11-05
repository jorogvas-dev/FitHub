"""Private fitness profiles and dated bodyweight measurements."""
from datetime import date
from decimal import Decimal, InvalidOperation
import re

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from psycopg.errors import UniqueViolation

from app.db import connect
from .auth import login_required
from .plans import query

bp = Blueprint('profile', __name__)

GOALS = (('strength', 'Strength'), ('muscle_gain', 'Muscle gain'),
         ('fat_loss', 'Fat loss'), ('general_fitness', 'General fitness'))
EXPERIENCE = (('beginner', 'Beginner'), ('intermediate', 'Intermediate'), ('advanced', 'Advanced'))


@bp.route('/onboarding', methods=['GET', 'POST'])
@login_required
def onboarding():
    today = date.today()
    with connect() as conn:
        # Serializes duplicate onboarding submissions across both replicas.
        if request.method == 'POST':
            conn.execute('SELECT user_id FROM app_user WHERE user_id=%s FOR UPDATE', (g.user['user_id'],))
        profile = query(conn, 'SELECT * FROM fitness_profile WHERE user_id=%s' +
                        (' FOR UPDATE' if request.method == 'POST' else ''), (g.user['user_id'],), one=True) or {}
        latest = query(conn, 'SELECT * FROM body_measurement WHERE user_id=%s ORDER BY measured_on DESC LIMIT 1',
                       (g.user['user_id'],), one=True)
        todays_weight = query(conn, 'SELECT * FROM body_measurement WHERE user_id=%s AND measured_on=%s',
                              (g.user['user_id'], today), one=True)
        if all(profile.get(field) is not None for field in ('height_cm', 'goal', 'experience')) and latest:
            return redirect(url_for('main.dashboard'))
        if request.method == 'POST':
            try:
                # Existing values always win, including values saved since this form was opened.
                height = profile.get('height_cm')
                if height is None:
                    height = decimal_input('height_cm', 'Height (cm)', 50, 300, 1)
                goal = profile.get('goal') or choice_input('goal', 'goal', GOALS)
                experience = profile.get('experience') or choice_input('experience', 'experience level', EXPERIENCE)
                if goal is None or experience is None:
                    raise ValueError('Choose your training goal and experience level.')
                if todays_weight:
                    if request.form.get('weight_action') != 'keep_today':
                        raise ValueError('A measurement already exists for today. Confirm that you want to keep it, or edit it in Bodyweight.')
                    weight = None
                elif latest and request.form.get('weight_action') == 'keep_latest':
                    weight = None
                else:
                    # Same weight/date validation as the bodyweight form, with today's date fixed.
                    weight = decimal_input('weight_kg', 'Weight (kg)', 20, 500, 2)
                try:
                    with conn.transaction():
                        conn.execute('INSERT INTO fitness_profile(user_id,height_cm,goal,experience) VALUES (%s,%s,%s,%s) '
                                     'ON CONFLICT (user_id) DO UPDATE SET height_cm=coalesce(fitness_profile.height_cm,EXCLUDED.height_cm),'
                                     'goal=coalesce(fitness_profile.goal,EXCLUDED.goal),experience=coalesce(fitness_profile.experience,EXCLUDED.experience)',
                                     (g.user['user_id'], height, goal, experience))
                        if weight is not None:
                            conn.execute('INSERT INTO body_measurement(user_id,measured_on,weight_kg) VALUES (%s,%s,%s)',
                                         (g.user['user_id'], today, weight))
                except UniqueViolation:
                    # A concurrent bodyweight form may have inserted today after our SELECT.
                    todays_weight = query(conn, 'SELECT * FROM body_measurement WHERE user_id=%s AND measured_on=%s',
                                          (g.user['user_id'], today), one=True)
                    raise ValueError('A measurement was just recorded for today. Review and confirm the existing entry.') from None
            except ValueError as exc:
                flash(str(exc))
            else:
                flash('You are ready to train. Create a plan or explore the exercise library.')
                return redirect(url_for('main.dashboard'))
    return render_template('profile/onboarding.html', profile=profile, latest=latest, todays_weight=todays_weight,
                           today=today, goals=GOALS, experience_levels=EXPERIENCE)


def decimal_input(field, label, low, high, places, optional=False):
    value = request.form.get(field, '').strip()
    if optional and not value:
        return None
    message = (f'{label} must be a finite decimal between {low} and {high}, '
               f'with at most {places} decimal place(s).')
    if not value or len(value) > 32:
        raise ValueError(message)
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError(message) from None
    # Check before comparing: Decimal NaN comparisons can raise InvalidOperation.
    if (not result.is_finite() or not low <= result <= high
            or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,' + str(places) + r'})?', value)):
        raise ValueError(message)
    return result


def choice_input(field, label, choices):
    value = request.form.get(field, '').strip()
    if value and value not in dict(choices):
        raise ValueError(f'Choose a valid {label}.')
    return value or None


def measurement_input():
    value = request.form.get('measured_on', '').strip()
    try:
        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
            raise ValueError
        measured_on = date.fromisoformat(value)
    except ValueError:
        raise ValueError('Enter a valid measurement date (YYYY-MM-DD).') from None
    if measured_on > date.today():
        raise ValueError('Measurement date cannot be in the future.')
    return measured_on, decimal_input('weight_kg', 'Weight (kg)', 20, 500, 2)


def owned_measurement(conn, measurement_id, lock=False):
    entry = query(conn, 'SELECT * FROM body_measurement WHERE measurement_id=%s AND user_id=%s' +
                  (' FOR UPDATE' if lock else ''), (measurement_id, g.user['user_id']), one=True)
    if not entry:
        abort(404)
    return entry


@bp.route('/profile', methods=['GET', 'POST'])
@login_required
def edit():
    with connect() as conn:
        profile = query(conn, 'SELECT height_cm,goal,experience FROM fitness_profile WHERE user_id=%s',
                        (g.user['user_id'],), one=True) or {}
        if request.method == 'POST':
            try:
                height = decimal_input('height_cm', 'Height (cm)', 50, 300, 1, optional=True)
                goal = choice_input('goal', 'goal', GOALS)
                experience = choice_input('experience', 'experience level', EXPERIENCE)
            except ValueError as exc:
                flash(str(exc))
            else:
                # Older accounts without a profile row can save one without changing their account.
                conn.execute('INSERT INTO fitness_profile(user_id,height_cm,goal,experience) VALUES (%s,%s,%s,%s) '
                             'ON CONFLICT (user_id) DO UPDATE SET height_cm=EXCLUDED.height_cm,'
                             'goal=EXCLUDED.goal,experience=EXCLUDED.experience',
                             (g.user['user_id'], height, goal, experience))
                flash('Fitness profile saved.')
                return redirect(url_for('profile.edit'))
    return render_template('profile/edit.html', profile=profile, goals=GOALS, experience_levels=EXPERIENCE)


@bp.get('/bodyweight')
@login_required
def history():
    with connect() as conn:
        measurements = query(conn, 'SELECT * FROM body_measurement WHERE user_id=%s ORDER BY measured_on DESC',
                             (g.user['user_id'],))
    return render_template('profile/history.html', measurements=measurements)


@bp.route('/bodyweight/new', methods=['GET', 'POST'])
@bp.route('/bodyweight/<int:measurement_id>/edit', methods=['GET', 'POST'])
@login_required
def measurement_edit(measurement_id=None):
    with connect() as conn:
        entry = owned_measurement(conn, measurement_id, lock=request.method == 'POST') if measurement_id is not None else None
        if request.method == 'POST':
            try:
                measured_on, weight = measurement_input()
            except ValueError as exc:
                flash(str(exc))
            else:
                try:
                    # A savepoint lets duplicate dates re-render the form after rollback.
                    with conn.transaction():
                        if entry is None:
                            conn.execute('INSERT INTO body_measurement(user_id,measured_on,weight_kg) VALUES (%s,%s,%s)',
                                         (g.user['user_id'], measured_on, weight))
                        else:
                            conn.execute('UPDATE body_measurement SET measured_on=%s,weight_kg=%s '
                                         'WHERE measurement_id=%s AND user_id=%s',
                                         (measured_on, weight, measurement_id, g.user['user_id']))
                except UniqueViolation:
                    flash('You already have a measurement for that date. Choose another date or edit the existing entry.')
                else:
                    flash('Bodyweight measurement saved.')
                    return redirect(url_for('profile.history'))
    return render_template('profile/measurement_form.html', entry=entry, today=date.today())


@bp.route('/bodyweight/<int:measurement_id>/delete', methods=['GET', 'POST'])
@login_required
def measurement_delete(measurement_id):
    with connect() as conn:
        entry = owned_measurement(conn, measurement_id, lock=request.method == 'POST')
        if request.method == 'POST':
            if request.form.get('confirm') != 'delete':
                abort(400)
            conn.execute('DELETE FROM body_measurement WHERE measurement_id=%s AND user_id=%s',
                         (measurement_id, g.user['user_id']))
            flash('Bodyweight measurement deleted.')
            return redirect(url_for('profile.history'))
    return render_template('profile/measurement_delete.html', entry=entry)
