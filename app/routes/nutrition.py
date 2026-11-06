"""Private manual food records and diary snapshots, with no external food service."""
from datetime import timedelta
from psycopg.types.json import Jsonb

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from app.db import connect
from app.services.nutrition import (CENT, DEFAULT_TIMEZONE, MAX_DATE, MEALS, MIN_DATE, NUTRIENTS,
                                    amount, daily_totals, diary_date, entry_totals,
                                    supported_timezones, target_comparison, user_today)
from .auth import login_required
from .plans import query
from .profile import decimal_input
from app.services.foods import ALLERGENS, available_foods, food_provenance, metadata_input

bp = Blueprint('nutrition', __name__, url_prefix='/nutrition')
FIELDS = tuple(key+'_100g' for key, _, _ in NUTRIENTS)


def settings_row(conn):
    row = dict(allergies='', unwanted_ingredients='', dietary_preferences='', timezone=DEFAULT_TIMEZONE,
               **{'target_'+key: None for key, _, _ in NUTRIENTS})
    stored = query(conn, 'SELECT * FROM nutrition_settings WHERE user_id=%s', (g.user['user_id'],), one=True)
    if stored:
        row.update(stored)
    return row


def date_or_400(value):
    try:
        return diary_date(value)
    except ValueError as exc:
        abort(400, description=str(exc))


def owned_food(conn, food_id, lock=''):
    food = query(conn, 'SELECT * FROM nutrition_food WHERE food_id=%s AND user_id=%s' + lock,
                 (food_id, g.user['user_id']), one=True)
    if not food:
        abort(404)
    return food


def owned_entry(conn, day, entry_id, lock=False):
    entry = query(conn, 'SELECT * FROM nutrition_diary_entry WHERE entry_id=%s AND user_id=%s AND logged_on=%s' +
                  (' FOR UPDATE' if lock else ''), (entry_id, g.user['user_id'], day), one=True)
    if not entry:
        abort(404)
    return entry


def food_input(name_field='name', places=2, name_limit=100):
    name = request.form.get(name_field, '').strip()
    if not 1 <= len(name) <= name_limit:
        raise ValueError(f'Food name must contain 1–{name_limit} characters.')
    rates = {}
    for key, label, _ in NUTRIENTS:
        rates[key+'_100g'] = decimal_input(key+'_100g', label+' per 100 g', 0,
                                           1000 if key == 'kcal' else 100, places, optional=True)
    return name, rates


def portion_input():
    day = diary_date(request.form.get('logged_on', ''))
    meal = request.form.get('meal', '')
    if meal not in dict(MEALS):
        raise ValueError('Choose breakfast, lunch, dinner or snack.')
    grams = decimal_input('grams', 'Grams consumed', CENT, 10000, 2)
    return day, meal, grams


def insert_food(conn, name, rates):
    return query(conn, 'INSERT INTO nutrition_food(user_id,name,kcal_100g,protein_100g,carbs_100g,fat_100g) '
                 'VALUES (%s,%s,%s,%s,%s,%s) RETURNING food_id',
                 (g.user['user_id'], name, *(rates[field] for field in FIELDS)), one=True)['food_id']


def insert_entry(conn, day, meal, grams, name, rates, food_id=None, catalogue_id=None, provenance=None):
    return query(conn, 'INSERT INTO nutrition_diary_entry(user_id,logged_on,meal,grams,food_name,food_id,'
                 'kcal_100g,protein_100g,carbs_100g,fat_100g,catalogue_id,provenance) '
                 'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING entry_id',
                 (g.user['user_id'], day, meal, grams, name, food_id, *(rates[field] for field in FIELDS),
                  catalogue_id, Jsonb(provenance or {})), one=True)['entry_id']


@bp.get('')
@login_required
def diary():
    with connect() as conn:
        settings = settings_row(conn)
        today = user_today(settings['timezone'])
        day = date_or_400(request.args.get('date', today.isoformat()))
        entries = query(conn, 'SELECT * FROM nutrition_diary_entry WHERE user_id=%s AND logged_on=%s '
                        'ORDER BY entry_id', (g.user['user_id'], day))
    totals = daily_totals(entries, settings)
    groups = {key: [] for key, _ in MEALS}
    for entry in entries:
        entry['totals'] = entry_totals(entry)
        groups[entry['meal']].append(entry)
    return render_template('nutrition/diary.html', settings=settings, day=day, today=today, groups=groups,
                           totals=totals, meals=MEALS, nutrients=NUTRIENTS, comparison=target_comparison(settings),
                           previous=day-timedelta(days=1) if day > MIN_DATE else None,
                           following=day+timedelta(days=1) if day < MAX_DATE else None)


@bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    with connect() as conn:
        stored = settings_row(conn)
        if request.method == 'POST':
            try:
                notes = []
                for field in ('allergies', 'unwanted_ingredients', 'dietary_preferences'):
                    value = request.form.get(field, '').strip()
                    if len(value) > 2000:
                        raise ValueError('Each allergy, unwanted ingredient or preference field allows up to 2000 characters.')
                    notes.append(value)
                zone = request.form.get('timezone', '').strip()
                if zone not in supported_timezones():
                    raise ValueError('Choose a supported IANA timezone, such as Europe/Sofia.')
                targets = [decimal_input('target_'+key, label+' daily target', 0,
                                          20000 if key == 'kcal' else 2000, 2, optional=True)
                           for key, label, _ in NUTRIENTS]
            except ValueError as exc:
                flash(str(exc))
            else:
                conn.execute('INSERT INTO nutrition_settings(user_id,allergies,unwanted_ingredients,dietary_preferences,'
                             'timezone,target_kcal,target_protein,target_carbs,target_fat) '
                             'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (user_id) DO UPDATE SET '
                             'allergies=EXCLUDED.allergies,unwanted_ingredients=EXCLUDED.unwanted_ingredients,'
                             'dietary_preferences=EXCLUDED.dietary_preferences,timezone=EXCLUDED.timezone,'
                             'target_kcal=EXCLUDED.target_kcal,target_protein=EXCLUDED.target_protein,'
                             'target_carbs=EXCLUDED.target_carbs,target_fat=EXCLUDED.target_fat',
                             (g.user['user_id'], *notes, zone, *targets))
                flash('Nutrition settings saved. Targets stay exactly as entered.')
                return redirect(url_for('nutrition.settings'))
    return render_template('nutrition/settings.html', settings=stored, zones=supported_timezones(),
                           nutrients=NUTRIENTS, comparison=target_comparison(stored))


@bp.get('/foods')
@login_required
def foods():
    search = request.args.get('q', '').strip()[:100]
    scope = request.args.get('scope', 'mine')
    if scope not in ('mine', 'library', 'all'):
        abort(400)
    preparation = request.args.get('preparation', '')
    if preparation not in ('', 'raw', 'uncooked', 'cooked', 'as described'):
        abort(400)
    sort = request.args.get('sort', 'name')
    direction = request.args.get('direction', 'asc')
    if sort not in ('name', 'kcal', 'protein', 'carbs', 'fat') or direction not in ('asc', 'desc'):
        abort(400)
    with connect() as conn:
        settings = settings_row(conn)
        today = user_today(settings['timezone'])
        day = date_or_400(request.args.get('date', today.isoformat()))
        rows = available_foods(conn, search, scope, preparation, sort, direction)
    return render_template('nutrition/foods.html', foods=rows, query=search, day=day, nutrients=NUTRIENTS,
                           scope=scope, preparation=preparation, sort=sort, direction=direction)


@bp.route('/foods/new', methods=['GET', 'POST'])
@bp.route('/foods/<int:food_id>/edit', methods=['GET', 'POST'])
@login_required
def food_edit(food_id=None):
    with connect() as conn:
        food = owned_food(conn, food_id, ' FOR UPDATE' if request.method == 'POST' else '') if food_id is not None else None
        if request.method == 'POST':
            try:
                name, rates = food_input(places=6 if food and food['provenance'] else 2)
                metadata = metadata_input()
            except ValueError as exc:
                flash(str(exc))
            else:
                if food is None:
                    food_id = insert_food(conn, name, rates)
                else:
                    conn.execute('UPDATE nutrition_food SET name=%s,kcal_100g=%s,protein_100g=%s,carbs_100g=%s,'
                                 'fat_100g=%s WHERE food_id=%s AND user_id=%s',
                                 (name, *(rates[field] for field in FIELDS), food_id, g.user['user_id']))
                conn.execute('UPDATE nutrition_food SET allergens=%s,allergen_complete=%s,ingredient_labels=%s '
                             'WHERE food_id=%s AND user_id=%s',
                             (metadata['allergens'], metadata['allergen_complete'], metadata['ingredient_labels'],
                              food_id, g.user['user_id']))
                flash('Food saved. Existing diary entries keep their recorded values.')
                return redirect(url_for('nutrition.foods'))
    return render_template('nutrition/food_form.html', food=food, nutrients=NUTRIENTS, allergens=ALLERGENS,
                           precision=6 if food and food['provenance'] else 2)


@bp.route('/foods/<int:food_id>/log', methods=['GET', 'POST'])
@login_required
def food_log(food_id):
    with connect() as conn:
        # Serialize the snapshot against an edit to this saved food.
        food = owned_food(conn, food_id, ' FOR SHARE' if request.method == 'POST' else '')
        settings = settings_row(conn)
        day = date_or_400(request.args.get('date', user_today(settings['timezone']).isoformat()))
        if request.method == 'POST':
            try:
                selected, meal, grams = portion_input()
            except ValueError as exc:
                flash(str(exc))
            else:
                insert_entry(conn, selected, meal, grams, food['name'], food, food_id,
                             provenance=food_provenance(food))
                flash('Food logged with a copy of its current nutrition values.')
                return redirect(url_for('nutrition.diary', date=selected.isoformat()))
    return render_template('nutrition/log_food.html', food=food, day=day, meals=MEALS, nutrients=NUTRIENTS)


@bp.route('/diary/<day>/entries/new', methods=['GET', 'POST'])
@bp.route('/diary/<day>/entries/<int:entry_id>/edit', methods=['GET', 'POST'])
@login_required
def entry_edit(day, entry_id=None):
    parent_day = date_or_400(day)
    with connect() as conn:
        entry = owned_entry(conn, parent_day, entry_id, lock=request.method == 'POST') if entry_id is not None else None
        if request.method == 'POST':
            try:
                selected, meal, grams = portion_input()
                name, rates = food_input('food_name', places=6 if entry and entry['provenance'] else 2,
                                         name_limit=300 if entry and entry['provenance'] else 100)
            except ValueError as exc:
                flash(str(exc))
            else:
                if entry is None:
                    # Saving a reusable food and its first entry is a single transaction.
                    food_id = insert_food(conn, name, rates) if request.form.get('save_food') == 'yes' else None
                    insert_entry(conn, selected, meal, grams, name, rates, food_id)
                else:
                    # Edit the snapshot itself, never fetch replacement values from a linked food.
                    provenance = dict(entry['provenance'])
                    if provenance:
                        provenance['manually_edited'] = True
                    conn.execute('UPDATE nutrition_diary_entry SET logged_on=%s,meal=%s,grams=%s,food_name=%s,'
                                 'kcal_100g=%s,protein_100g=%s,carbs_100g=%s,fat_100g=%s,provenance=%s '
                                 'WHERE entry_id=%s AND user_id=%s AND logged_on=%s',
                                 (selected, meal, grams, name, *(rates[field] for field in FIELDS),
                                  Jsonb(provenance), entry_id, g.user['user_id'], parent_day))
                flash('Diary entry saved.')
                return redirect(url_for('nutrition.diary', date=selected.isoformat()))
    return render_template('nutrition/entry_form.html', entry=entry, day=parent_day, meals=MEALS, nutrients=NUTRIENTS,
                           precision=6 if entry and entry['provenance'] else 2)


@bp.route('/diary/<day>/entries/<int:entry_id>/delete', methods=['GET', 'POST'])
@login_required
def entry_delete(day, entry_id):
    parent_day = date_or_400(day)
    with connect() as conn:
        entry = owned_entry(conn, parent_day, entry_id, lock=request.method == 'POST')
        if request.method == 'POST':
            if request.form.get('confirm') != 'delete':
                abort(400, description='Confirm deletion using the checkbox.')
            conn.execute('DELETE FROM nutrition_diary_entry WHERE entry_id=%s AND user_id=%s AND logged_on=%s',
                         (entry_id, g.user['user_id'], parent_day))
            flash('Diary entry deleted.')
            return redirect(url_for('nutrition.diary', date=parent_day.isoformat()))
    return render_template('nutrition/delete.html', entry=entry)


@bp.app_template_filter('nutrition_amount')
def nutrition_amount(value):
    return amount(value)
