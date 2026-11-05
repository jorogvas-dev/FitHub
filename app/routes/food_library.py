"""Read shared foods; only administrators can change their values or availability."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from psycopg.types.json import Jsonb
from app.db import connect
from app.services.foods import ALLERGENS, food_provenance, metadata_input
from app.services.nutrition import MEALS, NUTRIENTS, user_today
from .auth import admin_required, login_required
from .plans import query
from .nutrition import (FIELDS, date_or_400, food_input, insert_entry, insert_food,
                        portion_input, settings_row)

bp = Blueprint('food_library', __name__, url_prefix='/nutrition/library')


def catalogue_row(conn, identifier, lock='', admin=False):
    row = query(conn, 'SELECT * FROM food_catalogue WHERE catalogue_id=%s'+
                ('' if admin else ' AND is_active')+lock, (identifier,), one=True)
    if not row:
        abort(404)
    return row


@bp.route('/<int:food_id>/log', methods=['GET', 'POST'])
@login_required
def log(food_id):
    with connect() as conn:
        food = catalogue_row(conn, food_id, ' FOR SHARE' if request.method == 'POST' else '')
        settings = settings_row(conn)
        day = date_or_400(request.args.get('date', user_today(settings['timezone']).isoformat()))
        if request.method == 'POST':
            try:
                selected, meal, grams = portion_input()
            except ValueError as exc:
                flash(str(exc))
            else:
                insert_entry(conn, selected, meal, grams, food['name'], food, catalogue_id=food_id,
                             provenance=food_provenance(food))
                flash('Library food logged as a nutrition snapshot.')
                return redirect(url_for('nutrition.diary', date=selected.isoformat()))
    return render_template('nutrition/log_food.html', food=food, day=day, meals=MEALS, nutrients=NUTRIENTS)


@bp.post('/<int:food_id>/copy')
@login_required
def copy(food_id):
    with connect() as conn:
        food = catalogue_row(conn, food_id, ' FOR SHARE')
        identifier = insert_food(conn, food['name'][:100], food)
        # A copy is editable; the full original description remains in its provenance.
        conn.execute('UPDATE nutrition_food SET provenance=%s,allergens=%s,ingredient_labels=%s '
                     'WHERE food_id=%s AND user_id=%s',
                     (Jsonb(food_provenance(food)), food['allergens'], food['ingredient_labels'], identifier, g.user['user_id']))
    flash('Copied to My foods. Review ingredients and allergens independently.')
    return redirect(url_for('nutrition.food_edit', food_id=identifier))


@bp.get('/admin')
@admin_required
def admin_list():
    with connect() as conn:
        rows = query(conn, 'SELECT * FROM food_catalogue WHERE name ILIKE %s ORDER BY name,catalogue_id',
                     ('%'+request.args.get('q', '').strip()[:100]+'%',))
    return render_template('nutrition/library_admin.html', foods=rows)


@bp.route('/<int:food_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit(food_id):
    with connect() as conn:
        food = catalogue_row(conn, food_id, ' FOR UPDATE' if request.method == 'POST' else '', admin=True)
        if request.method == 'POST':
            try:
                name, rates = food_input(places=6, name_limit=300)
                metadata = metadata_input()
                preparation = request.form.get('preparation', '')
                if preparation not in ('raw', 'uncooked', 'cooked', 'as described'):
                    raise ValueError('Choose a preparation description.')
            except ValueError as exc:
                flash(str(exc))
            else:
                provenance = food_provenance(food)
                provenance['administrator_edited'] = True
                conn.execute('UPDATE food_catalogue SET name=%s,kcal_100g=%s,protein_100g=%s,carbs_100g=%s,'
                             'fat_100g=%s,preparation=%s,allergens=%s,allergen_complete=%s,ingredient_labels=%s,'
                             'provenance=%s WHERE catalogue_id=%s',
                             (name, *(rates[field] for field in FIELDS), preparation, metadata['allergens'],
                              metadata['allergen_complete'], metadata['ingredient_labels'], Jsonb(provenance), food_id))
                flash('Library food updated. Historical snapshots are unchanged.')
                return redirect(url_for('food_library.admin_list'))
    return render_template('nutrition/library_form.html', food=food, nutrients=NUTRIENTS,
                           allergens=ALLERGENS, precision=6)


@bp.route('/<int:food_id>/status', methods=['GET', 'POST'])
@admin_required
def status(food_id):
    with connect() as conn:
        food = catalogue_row(conn, food_id, ' FOR UPDATE' if request.method == 'POST' else '', admin=True)
        if request.method == 'POST':
            if request.form.get('confirm') != 'change':
                abort(400, description='Confirm the library availability change.')
            conn.execute('UPDATE food_catalogue SET is_active=NOT is_active WHERE catalogue_id=%s', (food_id,))
            return redirect(url_for('food_library.admin_list'))
    return render_template('nutrition/library_status.html', food=food)
