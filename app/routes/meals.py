"""Private recipe snapshots and reviewable plans; diary writes require Log meal."""
from decimal import Decimal
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from psycopg.types.json import Jsonb
from app.db import connect
from app.services.foods import (ALLERGENS, DIETS, available_foods, metadata_input, selected_food)
from app.services.meal_planner import (PORTIONS, differences, exact_match, exclusion_reasons,
    food_snapshot, generate, portion_entries, recipe_snapshot, restrictions, totals)
from app.services.nutrition import MEALS, NUTRIENTS, user_today
from .auth import login_required
from .nutrition import date_or_400, insert_entry, settings_row
from .plans import integer_input, query
from .profile import decimal_input

bp = Blueprint('meals', __name__, url_prefix='/nutrition')


def owned_recipe(conn, recipe_id, lock=''):
    row = query(conn, 'SELECT * FROM meal_recipe WHERE recipe_id=%s AND user_id=%s'+lock,
                (recipe_id,g.user['user_id']), one=True)
    if not row:
        abort(404)
    return row


def ingredients(conn, recipe_id):
    return query(conn, 'SELECT * FROM recipe_ingredient WHERE recipe_id=%s AND user_id=%s ORDER BY ingredient_id',
                 (recipe_id,g.user['user_id']))


def owned_ingredient(conn, recipe_id, ingredient_id):
    row = query(conn, 'SELECT * FROM recipe_ingredient WHERE ingredient_id=%s AND recipe_id=%s AND user_id=%s',
                (ingredient_id,recipe_id,g.user['user_id']), one=True)
    if not row:
        abort(404)
    return row


def recipe_input():
    name = request.form.get('name','').strip()
    if not 1 <= len(name) <= 100:
        raise ValueError('Recipe name must contain 1–100 characters.')
    servings = integer_input('servings','Batch servings',1,20)
    meals = sorted(set(request.form.getlist('meals')))
    diets = sorted(set(request.form.getlist('dietary_tags')))
    if not meals or not set(meals).issubset(dict(MEALS)):
        raise ValueError('Choose at least one supported meal slot.')
    if not set(diets).issubset(DIETS):
        raise ValueError('Choose supported dietary preference tags.')
    metadata = metadata_input()
    return (name,servings,meals,diets,metadata['allergens'],metadata['allergen_complete'])


@bp.get('/recipes')
@login_required
def recipes():
    with connect() as conn:
        rows = query(conn,'SELECT * FROM meal_recipe WHERE user_id=%s ORDER BY name,recipe_id',(g.user['user_id'],))
    return render_template('meals/recipes.html',recipes=rows)


@bp.route('/recipes/new',methods=['GET','POST'])
@bp.route('/recipes/<int:recipe_id>/edit',methods=['GET','POST'])
@login_required
def recipe_edit(recipe_id=None):
    with connect() as conn:
        recipe = owned_recipe(conn,recipe_id,' FOR UPDATE' if request.method=='POST' else '') if recipe_id else None
        if request.method=='POST':
            # Serialize recipe-count checks for this owner without affecting other users.
            conn.execute('SELECT user_id FROM app_user WHERE user_id=%s FOR UPDATE',(g.user['user_id'],))
            try:
                values = recipe_input()
                if not recipe and query(conn,'SELECT count(*) AS n FROM meal_recipe WHERE user_id=%s',
                                        (g.user['user_id'],),one=True)['n'] >= 100:
                    raise ValueError('Up to 100 recipes per account are supported in this phase.')
            except ValueError as exc:
                flash(str(exc))
            else:
                if recipe:
                    conn.execute('UPDATE meal_recipe SET name=%s,servings=%s,meals=%s,dietary_tags=%s,allergens=%s,'
                                 'allergen_complete=%s WHERE recipe_id=%s AND user_id=%s',
                                 (*values,recipe_id,g.user['user_id']))
                else:
                    recipe_id = query(conn,'INSERT INTO meal_recipe(name,servings,meals,dietary_tags,allergens,'
                                      'allergen_complete,user_id) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING recipe_id',
                                      (*values,g.user['user_id']),one=True)['recipe_id']
                flash('Recipe saved. Saved meal plans keep their snapshots.')
                return redirect(url_for('meals.recipe_detail',recipe_id=recipe_id))
    return render_template('meals/recipe_form.html',recipe=recipe,food=recipe,allergens=ALLERGENS,diets=DIETS,meals=MEALS,
                           metadata_labels=False)


@bp.get('/recipes/<int:recipe_id>')
@login_required
def recipe_detail(recipe_id):
    with connect() as conn:
        recipe = owned_recipe(conn,recipe_id)
        rows = ingredients(conn,recipe_id)
    snapshot = recipe_snapshot(recipe,rows)
    try:
        values = totals(portion_entries(snapshot,Decimal(1))) if rows else None
    except ValueError:
        values = None
    return render_template('meals/recipe.html',recipe=recipe,ingredients=rows,totals=values,nutrients=NUTRIENTS)


@bp.route('/recipes/<int:recipe_id>/delete',methods=['GET','POST'])
@login_required
def recipe_delete(recipe_id):
    with connect() as conn:
        recipe = owned_recipe(conn,recipe_id,' FOR UPDATE' if request.method=='POST' else '')
        if request.method=='POST':
            if request.form.get('confirm')!='delete':
                abort(400,description='Confirm recipe deletion.')
            conn.execute('DELETE FROM meal_recipe WHERE recipe_id=%s AND user_id=%s',(recipe_id,g.user['user_id']))
            flash('Recipe deleted. Plan and diary snapshots are preserved.')
            return redirect(url_for('meals.recipes'))
    return render_template('meals/delete.html',name=recipe['name'],back=url_for('meals.recipe_detail',recipe_id=recipe_id))


@bp.route('/recipes/<int:recipe_id>/ingredients/new',methods=['GET','POST'])
@bp.route('/recipes/<int:recipe_id>/ingredients/<int:ingredient_id>/edit',methods=['GET','POST'])
@login_required
def ingredient_edit(recipe_id,ingredient_id=None):
    with connect() as conn:
        recipe = owned_recipe(conn,recipe_id,' FOR UPDATE' if request.method=='POST' else '')
        ingredient = owned_ingredient(conn,recipe_id,ingredient_id) if ingredient_id else None
        search = request.args.get('q','').strip()[:100]
        scope = request.args.get('scope','all')
        preparation = request.args.get('preparation','')
        if scope not in ('all','mine','library') or preparation not in ('','raw','uncooked','cooked','as described'):
            abort(400)
        foods = available_foods(conn,search,scope,preparation)
        if request.method=='POST':
            try:
                grams = decimal_input('grams','Batch ingredient grams',Decimal('.01'),10000,2)
                metadata = metadata_input()
                food = selected_food(conn,request.form.get('food_token',''),lock=True)
                # Retain source declarations even if the ingredient form omits them.
                metadata['allergens'] = sorted(set(metadata['allergens']) | set(food['allergens']))
                if not ingredient and len(ingredients(conn,recipe_id)) >= 30:
                    raise ValueError('A recipe supports up to 30 ingredients.')
            except ValueError as exc:
                flash(str(exc))
            else:
                values = (food.get('food_id'),food.get('catalogue_id'),grams,Jsonb(food_snapshot(food)),
                          metadata['allergens'],metadata['allergen_complete'],metadata['ingredient_labels'])
                if ingredient:
                    conn.execute('UPDATE recipe_ingredient SET food_id=%s,catalogue_id=%s,grams=%s,food_snapshot=%s,'
                                 'allergens=%s,allergen_complete=%s,ingredient_labels=%s '
                                 'WHERE ingredient_id=%s AND recipe_id=%s AND user_id=%s',
                                 (*values,ingredient_id,recipe_id,g.user['user_id']))
                else:
                    conn.execute('INSERT INTO recipe_ingredient(food_id,catalogue_id,grams,food_snapshot,allergens,'
                                 'allergen_complete,ingredient_labels,recipe_id,user_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                                 (*values,recipe_id,g.user['user_id']))
                flash('Ingredient saved with the selected food values and your separate ingredient review.')
                return redirect(url_for('meals.recipe_detail',recipe_id=recipe_id))
    return render_template('meals/ingredient_form.html',recipe=recipe,ingredient=ingredient,food=ingredient,
                           foods=foods,allergens=ALLERGENS,scope=scope,preparation=preparation,query=search)


@bp.route('/recipes/<int:recipe_id>/ingredients/<int:ingredient_id>/delete',methods=['GET','POST'])
@login_required
def ingredient_delete(recipe_id,ingredient_id):
    with connect() as conn:
        owned_recipe(conn,recipe_id,' FOR UPDATE' if request.method=='POST' else '')
        row = owned_ingredient(conn,recipe_id,ingredient_id)
        if request.method=='POST':
            if request.form.get('confirm')!='delete':
                abort(400,description='Confirm ingredient deletion.')
            conn.execute('DELETE FROM recipe_ingredient WHERE ingredient_id=%s AND recipe_id=%s AND user_id=%s',
                         (ingredient_id,recipe_id,g.user['user_id']))
            return redirect(url_for('meals.recipe_detail',recipe_id=recipe_id))
    return render_template('meals/delete.html',name=row['food_snapshot']['name'],back=url_for('meals.recipe_detail',recipe_id=recipe_id))


def candidates(conn,rules):
    rows = query(conn,'SELECT * FROM meal_recipe WHERE user_id=%s ORDER BY name,recipe_id FOR SHARE',(g.user['user_id'],))
    eligible, excluded = [], []
    catalogue = {}
    for recipe in rows:
        children = ingredients(conn,recipe['recipe_id'])
        snapshot = recipe_snapshot(recipe,children)
        reasons = exclusion_reasons(snapshot,rules)
        for child in children:
            if child['catalogue_id'] is not None:
                identifier = child['catalogue_id']
                if identifier not in catalogue:
                    row = query(conn,'SELECT is_active FROM food_catalogue WHERE catalogue_id=%s FOR SHARE',(identifier,),one=True)
                    catalogue[identifier] = row and row['is_active']
                if not catalogue[identifier]:
                    reasons.append('An ingredient source is deactivated. Reselect an active food in this recipe.')
                    break
        if reasons:
            excluded.append(dict(name=recipe['name'],reasons=reasons))
        else:
            eligible.append(snapshot)
    return eligible,excluded


def target_snapshot(settings):
    return {key:None if settings['target_'+key] is None else str(settings['target_'+key]) for key,_,_ in NUTRIENTS}


@bp.get('/meal-plans')
@login_required
def plans():
    with connect() as conn:
        rows = query(conn,'SELECT * FROM meal_plan WHERE user_id=%s ORDER BY planned_on DESC,plan_id DESC',(g.user['user_id'],))
    return render_template('meals/plans.html',plans=rows)


@bp.route('/meal-plans/new',methods=['GET','POST'])
@login_required
def plan_new():
    with connect() as conn:
        settings = settings_row(conn)
        day = user_today(settings['timezone'])
        rules,errors = restrictions(settings)
        eligible,excluded = candidates(conn,rules) if not errors else ([],[])
        if request.method=='POST':
            try:
                day = date_or_400(request.form.get('planned_on',''))
                if errors:
                    raise ValueError('Clarify the restriction labels before planning; no exclusions were relaxed.')
                choices,notes = generate(eligible,target_snapshot(settings))
                if choices is None:
                    errors.extend(notes)
                else:
                    plan_id = query(conn,'INSERT INTO meal_plan(user_id,planned_on,targets,restrictions,search_note) '
                                    'VALUES (%s,%s,%s,%s,%s) RETURNING plan_id',
                                    (g.user['user_id'],day,Jsonb(target_snapshot(settings)),Jsonb(rules),'\n'.join(notes)),one=True)['plan_id']
                    for (meal,_),(snapshot,portion) in zip(MEALS,choices):
                        conn.execute('INSERT INTO meal_plan_meal(plan_id,user_id,meal,portion,recipe_snapshot) VALUES (%s,%s,%s,%s,%s)',
                                     (plan_id,g.user['user_id'],meal,portion,Jsonb(snapshot)))
                    return redirect(url_for('meals.plan_detail',plan_id=plan_id))
            except ValueError as exc:
                flash(str(exc))
    return render_template('meals/plan_new.html',day=day,settings=settings,errors=errors,excluded=excluded,
                           eligible=eligible,nutrients=NUTRIENTS,allergens=ALLERGENS)


def owned_plan(conn,plan_id,lock=''):
    row = query(conn,'SELECT * FROM meal_plan WHERE plan_id=%s AND user_id=%s'+lock,(plan_id,g.user['user_id']),one=True)
    if not row:
        abort(404)
    return row


def plan_meals(conn,plan_id):
    rows = query(conn,'SELECT * FROM meal_plan_meal WHERE plan_id=%s AND user_id=%s '
                 "ORDER BY CASE meal WHEN 'breakfast' THEN 1 WHEN 'lunch' THEN 2 WHEN 'dinner' THEN 3 ELSE 4 END",
                 (plan_id,g.user['user_id']))
    for row in rows:
        row['entries'] = portion_entries(row['recipe_snapshot'],row['portion'])
        row['totals'] = totals(row['entries'])
    return rows


def effective_rules(conn,plan):
    current,errors = restrictions(settings_row(conn))
    rules = {key:sorted(set(current[key])|set(plan['restrictions'][key])) for key in current}
    return rules,errors


def plan_errors(rows,rules,errors):
    result = list(errors)
    for row in rows:
        for reason in exclusion_reasons(row['recipe_snapshot'],rules):
            result.append(row['meal']+': '+reason)
    return result


@bp.get('/meal-plans/<int:plan_id>')
@login_required
def plan_detail(plan_id):
    with connect() as conn:
        plan = owned_plan(conn,plan_id)
        rows = plan_meals(conn,plan_id)
        rules,errors = effective_rules(conn,plan)
        errors = plan_errors(rows,rules,errors)
    total = totals([entry for row in rows for entry in row['entries']])
    return render_template('meals/plan.html',plan=plan,meals=rows,total=total,errors=errors,
                           differences=differences(total,plan['targets']),exact=exact_match(total,plan['targets']),nutrients=NUTRIENTS)


@bp.route('/meal-plans/<int:plan_id>/meals/<int:meal_id>/edit',methods=['GET','POST'])
@login_required
def meal_edit(plan_id,meal_id):
    with connect() as conn:
        plan = owned_plan(conn,plan_id,' FOR UPDATE' if request.method=='POST' else '')
        row = query(conn,'SELECT * FROM meal_plan_meal WHERE meal_id=%s AND plan_id=%s AND user_id=%s',
                    (meal_id,plan_id,g.user['user_id']),one=True)
        if not row:
            abort(404)
        if plan['status']!='draft':
            abort(409,description='Saved meal plans are read-only. Generate a new draft to make changes.')
        rules,errors = effective_rules(conn,plan)
        choices,excluded = candidates(conn,rules) if not errors else ([],[])
        choices = [choice for choice in choices if row['meal'] in choice['meals']]
        if request.method=='POST':
            try:
                if errors:
                    raise ValueError('Clarify current restriction labels before adjusting this draft.')
                portion = decimal_input('portion','Portion servings',Decimal('.25'),2,2)
                if portion not in PORTIONS:
                    raise ValueError('Choose 0.25–2 servings in steps of 0.25.')
                selected = request.form.get('recipe_id','snapshot')
                if selected=='snapshot':
                    snapshot = row['recipe_snapshot']
                    reasons = exclusion_reasons(snapshot,rules)
                    if reasons:
                        raise ValueError(' '.join(reasons))
                else:
                    snapshot = next((choice for choice in choices if str(choice['recipe_id'])==selected),None)
                    if snapshot is None:
                        abort(404)
                portion_entries(snapshot,portion)  # Validate final storage quantities before writing.
            except ValueError as exc:
                flash(str(exc))
            else:
                conn.execute('UPDATE meal_plan_meal SET portion=%s,recipe_snapshot=%s WHERE meal_id=%s AND plan_id=%s AND user_id=%s',
                             (portion,Jsonb(snapshot),meal_id,plan_id,g.user['user_id']))
                return redirect(url_for('meals.plan_detail',plan_id=plan_id))
    return render_template('meals/meal_form.html',plan=plan,meal=row,choices=choices,errors=errors,excluded=excluded,portions=PORTIONS)


@bp.post('/meal-plans/<int:plan_id>/save')
@login_required
def plan_save(plan_id):
    with connect() as conn:
        plan = owned_plan(conn,plan_id,' FOR UPDATE')
        if plan['status']!='draft':
            abort(409,description='This plan is already saved.')
        rows = plan_meals(conn,plan_id)
        rules,errors = effective_rules(conn,plan)
        errors = plan_errors(rows,rules,errors)
        if errors:
            abort(409,description='Cannot save: '+' '.join(errors))
        total = totals([entry for row in rows for entry in row['entries']])
        if not exact_match(total,plan['targets']) and request.form.get('accept_differences')!='yes':
            abort(400,description='Review and explicitly accept the target differences before saving.')
        conn.execute("UPDATE meal_plan SET status='saved',saved_at=now() WHERE plan_id=%s AND user_id=%s",
                     (plan_id,g.user['user_id']))
    flash('Plan saved. Your food diary has not changed. Use Log meal for each meal you consume.')
    return redirect(url_for('meals.plan_detail',plan_id=plan_id))


@bp.post('/meal-plans/<int:plan_id>/meals/<int:meal_id>/log')
@login_required
def meal_log(plan_id,meal_id):
    with connect() as conn:
        # All plan mutations lock the same parent, including duplicate log requests.
        plan = owned_plan(conn,plan_id,' FOR UPDATE')
        row = query(conn,'SELECT * FROM meal_plan_meal WHERE meal_id=%s AND plan_id=%s AND user_id=%s',
                    (meal_id,plan_id,g.user['user_id']),one=True)
        if not row:
            abort(404)
        if plan['status']!='saved':
            abort(409,description='Review and save the draft before logging meals.')
        if row['logged_at'] is not None:
            abort(409,description='This meal was already logged. Edit its existing diary entries instead.')
        if request.form.get('confirm')!='log':
            abort(400,description='Confirm that you want to log this meal on the plan date.')
        rules,errors = effective_rules(conn,plan)
        errors.extend(exclusion_reasons(row['recipe_snapshot'],rules))
        if errors:
            abort(409,description='Cannot log under current restrictions: '+' '.join(errors))
        for index,entry in enumerate(portion_entries(row['recipe_snapshot'],row['portion'])):
            identifier = insert_entry(conn,plan['planned_on'],row['meal'],entry['grams'],entry['food_name'],entry,
                                      provenance=entry['provenance'])
            conn.execute('UPDATE nutrition_diary_entry SET plan_meal_id=%s,plan_ingredient_index=%s WHERE entry_id=%s AND user_id=%s',
                         (meal_id,index,identifier,g.user['user_id']))
        conn.execute('UPDATE meal_plan_meal SET logged_at=now() WHERE meal_id=%s AND plan_id=%s AND user_id=%s',
                     (meal_id,plan_id,g.user['user_id']))
    flash('Meal logged from its saved ingredient and nutrition snapshots.')
    return redirect(url_for('nutrition.diary',date=plan['planned_on'].isoformat()))
