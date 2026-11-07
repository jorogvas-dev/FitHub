"""Private recipe snapshots and reviewable plans; diary writes require Log meal."""
from decimal import Decimal
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from psycopg.types.json import Jsonb
from app.db import connect
from app.services.foods import (ALLERGENS, DIETS, available_foods, metadata_input, selected_food)
from app.services.meal_planner import (PORTIONS, differences, exact_match, exclusion_reasons,
    food_snapshot, generate, portion_entries, recipe_snapshot, restrictions, totals)
from app.services.nutrition import MEALS, NUTRIENTS, user_today
from app.services.starter_recipes import candidates as starter_candidates
from app.services import recipe_catalogue, nutrition_ai, meal_swaps, nutrition_limits
from itsdangerous import BadSignature, SignatureExpired
import httpx
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
        rules,errors=restrictions(settings_row(conn))
        starters,excluded=starter_candidates(conn,rules) if not errors else ([],[])
    for recipe in starters:
        recipe['entries']=portion_entries(recipe,Decimal(1))
        recipe['totals']=totals(recipe['entries'])
    return render_template('meals/recipes.html',recipes=rows,starters=starters,excluded=excluded,errors=errors,nutrients=NUTRIENTS)


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


def candidates(conn,rules,include_starters=False,include_catalogue=False):
    rows = query(conn,'SELECT * FROM meal_recipe WHERE user_id=%s ORDER BY name,recipe_id FOR SHARE',(g.user['user_id'],))
    eligible, excluded = [], []
    catalogue = {}
    for recipe in rows:
        children = ingredients(conn,recipe['recipe_id'])
        snapshot = recipe_snapshot(recipe,children)
        reasons = exclusion_reasons(snapshot,rules,require_nutrition=False)
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
    if include_starters:
        starter_rows,starter_excluded=starter_candidates(conn,rules,require_nutrition=False)
        eligible.extend(starter_rows)
        excluded.extend(starter_excluded)
    if include_catalogue:
        shared,shared_excluded=recipe_catalogue.eligible(conn,rules,require_nutrition=False)
        eligible.extend(shared)
        excluded.extend(shared_excluded)
    return checkable_candidates(eligible,excluded,target_snapshot(settings_row(conn)))


def checkable_candidates(eligible,excluded,targets):
    result=[]
    for recipe in eligible:
        values=None
        for portion in PORTIONS:
            try:values=totals(portion_entries(recipe,portion));break
            except ValueError:continue
        missing=[label for key,label,_ in NUTRIENTS if targets.get(key) is not None and (values is None or values[key] is None)]
        if missing:excluded.append(dict(name=recipe['name'],reasons=['Unknown nutrition values prevent checking configured limits: '+', '.join(missing)+'.']))
        else:result.append(recipe)
    return result,excluded


def target_snapshot(settings):
    return {key:None if settings['target_'+key] is None else str(settings['target_'+key]) for key,_,_ in NUTRIENTS}


def lock_limits(conn):
    # Settings writes take FOR UPDATE on the same user, including first insertion.
    conn.execute('SELECT user_id FROM app_user WHERE user_id=%s FOR SHARE',(g.user['user_id'],))


def limits_state(conn,plan):
    active=target_snapshot(settings_row(conn))
    rules,errors=effective_rules(conn,plan)
    rows=plan_meals(conn,plan['plan_id'])
    state=meal_swaps.fingerprint(dict(user_id=g.user['user_id'],plan_id=plan['plan_id'],status=plan['status'],
        targets=active,rules=rules,meals=[dict(id=row['meal_id'],version=row['version'],portion=str(row['portion']),snapshot=row['recipe_snapshot']) for row in rows]))
    return active,rows,state


def limits_token(state):
    return meal_swaps.serializer().dumps(dict(purpose='nutrition_limits',state=state))


def validate_limits_preview(token,state):
    try:value=meal_swaps.serializer().loads(token,max_age=1800)
    except SignatureExpired:abort(409,description='This preview expired. Refresh the plan before saving.')
    except BadSignature:abort(400,description='Invalid plan preview. Reload this form.')
    if not isinstance(value,dict) or value.get('purpose')!='nutrition_limits':abort(400,description='Invalid plan preview.')
    if value.get('state')!=state:abort(409,description='The plan, targets or exclusions changed. Refresh this preview before saving.')


def projected_total(rows,meal_id,snapshot,portion):
    return totals([entry for row in rows for entry in
                   (portion_entries(snapshot,portion) if row['meal_id']==meal_id else row['entries'])])


@bp.get('/meal-plans')
@login_required
def plans():
    with connect() as conn:
        rows = query(conn,'SELECT * FROM meal_plan WHERE user_id=%s ORDER BY planned_on DESC,plan_id DESC',(g.user['user_id'],))
    return render_template('meals/plans.html',plans=rows)


def plan_name_input():
    name=request.form.get('name','').strip()
    if len(name)>100 or any(ord(character)<32 for character in name):
        raise ValueError('Plan name must be at most 100 characters, on one line.')
    return name or None


@bp.route('/meal-plans/<int:plan_id>/name',methods=['GET','POST'])
@login_required
def plan_name(plan_id):
    with connect() as conn:
        plan=owned_plan(conn,plan_id,' FOR UPDATE' if request.method=='POST' else '')
        if request.method=='POST':
            try:name=plan_name_input()
            except ValueError as exc:flash(str(exc))
            else:
                conn.execute('UPDATE meal_plan SET name=%s WHERE plan_id=%s AND user_id=%s',(name,plan_id,g.user['user_id']))
                return redirect(url_for('meals.plan_detail',plan_id=plan_id))
    return render_template('meals/plan_name.html',plan=plan)


@bp.route('/meal-plans/new',methods=['GET','POST'])
@login_required
def plan_new():
    with connect() as conn:
        if request.method=='POST':lock_limits(conn)
        settings = settings_row(conn)
        has_recipes=query(conn,'SELECT EXISTS(SELECT 1 FROM meal_recipe WHERE user_id=%s) AS value',
                          (g.user['user_id'],),one=True)['value']
        include_starters=(request.form.get('include_starters')=='yes' if request.method=='POST'
                          else request.args.get('starters')=='yes' or not has_recipes)
        day = user_today(settings['timezone'])
        rules,errors = restrictions(settings)
        eligible,excluded = candidates(conn,rules,include_starters) if not errors else ([],[])
        if request.method=='POST':
            try:
                day = date_or_400(request.form.get('planned_on',''))
                name=plan_name_input()
                if errors:
                    raise ValueError('Clarify the restriction labels before planning; no exclusions were relaxed.')
                choices,notes = generate(eligible,target_snapshot(settings))
                if choices is None:
                    errors.extend(notes)
                else:
                    plan_id = query(conn,'INSERT INTO meal_plan(user_id,planned_on,targets,restrictions,search_note,include_starters,name) '
                                    'VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING plan_id',
                                    (g.user['user_id'],day,Jsonb(target_snapshot(settings)),Jsonb(rules),'\n'.join(notes),include_starters,name),one=True)['plan_id']
                    for (meal,_),choice in zip(MEALS,choices):
                        if choice is None:continue
                        snapshot,portion=choice
                        conn.execute('INSERT INTO meal_plan_meal(plan_id,user_id,meal,portion,recipe_snapshot) VALUES (%s,%s,%s,%s,%s)',
                                     (plan_id,g.user['user_id'],meal,portion,Jsonb(snapshot)))
                    return redirect(url_for('meals.plan_detail',plan_id=plan_id))
            except ValueError as exc:
                flash(str(exc))
    return render_template('meals/plan_new.html',day=day,settings=settings,errors=errors,excluded=excluded,
                           eligible=eligible,nutrients=NUTRIENTS,allergens=ALLERGENS,include_starters=include_starters)


def owned_plan(conn,plan_id,lock=''):
    row = query(conn,'SELECT * FROM meal_plan WHERE plan_id=%s AND user_id=%s'+lock,(plan_id,g.user['user_id']),one=True)
    if not row:
        abort(404)
    return row


@bp.route('/meal-plans/ai',methods=['GET','POST'])
@login_required
def ai_plan():
    errors,excluded=[],[]
    text=request.form.get('meal_style','Higher protein, quick breakfast and meal prep').strip()[:200]
    with connect() as conn:
        settings=settings_row(conn)
        day=user_today(settings['timezone'])
        rules,errors=restrictions(settings)
        eligible,excluded=recipe_catalogue.eligible(conn,rules,require_nutrition=False) if not errors else ([],[])
        eligible,excluded=checkable_candidates(eligible,excluded,target_snapshot(settings))
        snack_rows,_=candidates(conn,rules,include_starters=True) if not errors else ([],[])
        snack_rows=[row for row in snack_rows if 'snack' in row['meals']]
    if request.method=='POST':
        try:
            day=date_or_400(request.form.get('planned_on',''))
            name=plan_name_input()
            if errors:raise ValueError('Clarify saved restrictions before planning; no exclusions were relaxed.')
            # Filtering already ran. Only eligible IDs constrain the vector query.
            ranked=nutrition_ai.shortlist(eligible,text,target_snapshot(settings))
            choices,notes=nutrition_ai.generate(ranked,target_snapshot(settings),text,with_notes=True,snacks=snack_rows,candidates=eligible)
            with connect() as conn:
                conn.execute('SELECT user_id FROM app_user WHERE user_id=%s FOR UPDATE',(g.user['user_id'],))
                current=settings_row(conn)
                if current!=settings:raise ValueError('Preferences or targets changed during generation. Generate again.')
                safe,_=recipe_catalogue.eligible(conn,rules,require_nutrition=False)
                current_snacks,_=candidates(conn,rules,include_starters=True)
                safe.extend(row for row in current_snacks if 'snack' in row['meals'])
                current_by_id={row['recipe_id']:row for row in safe}
                if any(current_by_id.get(choice[0]['recipe_id'])!=choice[0] for choice in choices if choice):
                    raise ValueError('Recipe eligibility or source data changed. Generate again; exclusions were not relaxed.')
                plan_id=query(conn,'INSERT INTO meal_plan(user_id,planned_on,targets,restrictions,search_note,include_catalogue,include_starters,name) '
                    'VALUES (%s,%s,%s,%s,%s,true,true,%s) RETURNING plan_id',
                    (g.user['user_id'],day,Jsonb(target_snapshot(settings)),Jsonb(rules),
                     '\n'.join(['FitHub Coach preferences were fitted by the server under every configured upper limit.']+notes),name),one=True)['plan_id']
                for (meal,_),choice in zip(MEALS,choices):
                    if choice is None:continue
                    snapshot,portion=choice
                    conn.execute('INSERT INTO meal_plan_meal(plan_id,user_id,meal,portion,recipe_snapshot) VALUES (%s,%s,%s,%s,%s)',
                        (plan_id,g.user['user_id'],meal,portion,Jsonb(snapshot)))
            return redirect(url_for('meals.plan_detail',plan_id=plan_id))
        except (ValueError,httpx.HTTPError,KeyError,TypeError) as exc:
            errors.append(str(exc) if isinstance(exc,ValueError) and not isinstance(exc,(httpx.HTTPError,nutrition_ai.workout_ai.InferenceError)) else
                          'Recipe search or AI is unavailable. Use the manual planner or try again. No draft was created.')
    return render_template('meals/ai_plan.html',day=day,settings=settings,errors=errors,excluded=excluded,eligible=eligible,meal_style=text)


def current_catalogue_errors(conn,rows,rules):
    shared=[row for row in rows if row['recipe_snapshot'].get('catalogue_recipe') or row['recipe_snapshot'].get('starter')]
    if not shared:return []
    eligible,_=recipe_catalogue.eligible(conn,rules,require_nutrition=False)
    starters,_=starter_candidates(conn,rules,require_nutrition=False)
    eligible.extend(starters)
    identifiers={row['recipe_id'] for row in eligible}
    return [row['meal']+': source recipe is inactive or blocked under current ingredient checks.'
            for row in shared if row['recipe_snapshot']['recipe_id'] not in identifiers]


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
        for reason in exclusion_reasons(row['recipe_snapshot'],rules,require_nutrition=False):
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
        errors.extend(current_catalogue_errors(conn,rows,rules))
        active,rows,state=limits_state(conn,plan)
    total = totals([entry for row in rows for entry in row['entries']])
    errors.extend(nutrition_limits.errors(total,active))
    return render_template('meals/plan.html',plan=plan,meals=rows,total=total,errors=errors,
                           differences=differences(total,active),exact=exact_match(total,active),nutrients=NUTRIENTS,
                           active_targets=active,remaining=nutrition_limits.remaining(total,active),save_preview=limits_token(state))


@bp.route('/meal-plans/<int:plan_id>/meals/<int:meal_id>/edit',methods=['GET','POST'])
@login_required
def meal_edit(plan_id,meal_id):
    with connect() as conn:
        if request.method=='POST':lock_limits(conn)
        plan = owned_plan(conn,plan_id,' FOR UPDATE' if request.method=='POST' else '')
        row = query(conn,'SELECT * FROM meal_plan_meal WHERE meal_id=%s AND plan_id=%s AND user_id=%s',
                    (meal_id,plan_id,g.user['user_id']),one=True)
        if not row:
            abort(404)
        if plan['status']!='draft':
            abort(409,description='Saved meal plans are read-only. Generate a new draft to make changes.')
        rules,errors = effective_rules(conn,plan)
        choices,excluded = candidates(conn,rules,plan['include_starters'],plan['include_catalogue']) if not errors else ([],[])
        choices = [choice for choice in choices if row['meal'] in choice['meals']]
        active,all_rows,state=limits_state(conn,plan)
        if request.method=='POST':
            try:
                if errors:
                    raise ValueError('Clarify current restriction labels before adjusting this draft.')
                validate_limits_preview(request.form.get('limits_preview',''),state)
                portion = decimal_input('portion','Portion servings',Decimal('.25'),2,2)
                if portion not in PORTIONS:
                    raise ValueError('Choose 0.25–2 servings in steps of 0.25.')
                selected = request.form.get('recipe_id','snapshot')
                if selected=='snapshot':
                    snapshot = row['recipe_snapshot']
                    reasons = exclusion_reasons(snapshot,rules,require_nutrition=False)
                    reasons.extend(current_catalogue_errors(conn,[row],rules))
                    if reasons:
                        raise ValueError(' '.join(reasons))
                else:
                    snapshot = next((choice for choice in choices if str(choice['recipe_id'])==selected),None)
                    if snapshot is None:
                        abort(404)
                portion_entries(snapshot,portion)  # Validate final storage quantities before writing.
                problems=nutrition_limits.errors(projected_total(all_rows,meal_id,snapshot,portion),active)
                if problems:raise ValueError('Cannot update draft: '+' '.join(problems))
            except ValueError as exc:
                flash(str(exc))
            else:
                conn.execute('UPDATE meal_plan_meal SET portion=%s,recipe_snapshot=%s,version=version+1 WHERE meal_id=%s AND plan_id=%s AND user_id=%s',
                             (portion,Jsonb(snapshot),meal_id,plan_id,g.user['user_id']))
                return redirect(url_for('meals.plan_detail',plan_id=plan_id))
    return render_template('meals/meal_form.html',plan=plan,meal=row,choices=choices,errors=errors,excluded=excluded,portions=PORTIONS,limits_preview=limits_token(state))


def owned_meal(conn,plan_id,meal_id,lock=''):
    row=query(conn,'SELECT * FROM meal_plan_meal WHERE meal_id=%s AND plan_id=%s AND user_id=%s'+lock,
              (meal_id,plan_id,g.user['user_id']),one=True)
    if not row:abort(404)
    return row


@bp.route('/meal-plans/<int:plan_id>/meals/<int:meal_id>/swap',methods=['GET','POST'])
@login_required
def meal_swap(plan_id,meal_id):
    goal=(request.form if request.method=='POST' else request.args).get('goal','similar')
    if goal not in meal_swaps.GOALS:abort(400,description='Choose a supported meal comparison.')
    with connect() as conn:
        plan=owned_plan(conn,plan_id)
        row=owned_meal(conn,plan_id,meal_id)
        if plan['status']!='saved':abort(409,description='Save the draft before using Swap this meal.')
        rules,errors=effective_rules(conn,plan)
        active,all_rows,state=limits_state(conn,plan)
        other=totals([entry for item in all_rows if item['meal_id']!=meal_id for entry in item['entries']])
        baseline,options,note=meal_swaps.alternatives(conn,row,rules,goal,active,other) if not errors else (None,[],None)
        for option in options:option['token']=meal_swaps.token_for(g.user['user_id'],plan,row,option,goal,state)
    explanations,coach_note={},None
    if request.method=='POST' and options:
        explanations,coach_note=meal_swaps.explain(baseline,options)
    return render_template('meals/swap.html',plan=plan,meal=row,baseline=baseline,options=options,goal=goal,
        goals=meal_swaps.GOALS,errors=errors,note=note,explanations=explanations,coach_note=coach_note,nutrients=NUTRIENTS)


@bp.post('/meal-plans/<int:plan_id>/meals/<int:meal_id>/replace')
@login_required
def meal_replace(plan_id,meal_id):
    with connect() as conn:
        lock_limits(conn)
        plan=owned_plan(conn,plan_id,' FOR UPDATE')
        row=owned_meal(conn,plan_id,meal_id,' FOR UPDATE')
        if plan['status']!='saved':abort(409,description='Only saved meal plans support confirmed swaps.')
        if request.form.get('confirm')!='replace':abort(400,description='Explicitly confirm Replace meal.')
        try:
            active,all_rows,state=limits_state(conn,plan)
            preview=meal_swaps.validate_token(request.form.get('preview',''),g.user['user_id'],plan,row,state)
            rules,errors=effective_rules(conn,plan)
            if errors:raise meal_swaps.StalePreview('Your restriction labels changed. Review preferences and refresh alternatives.')
            other=totals([entry for item in all_rows if item['meal_id']!=meal_id for entry in item['entries']])
            baseline,options,note=meal_swaps.alternatives(conn,row,rules,preview['goal'],active,other)
            chosen=next((option for option in options if option['recipe']['recipe_id']==preview['recipe_id']
                and str(option['portion'])==preview['portion'] and meal_swaps.fingerprint(option['recipe'])==preview.get('candidate')),None)
            if chosen is None:raise meal_swaps.StalePreview('This alternative changed or is no longer eligible. Refresh alternatives.')
            if nutrition_limits.errors(projected_total(all_rows,meal_id,chosen['recipe'],chosen['portion']),active):
                raise meal_swaps.StalePreview('Current daily limits prevent this replacement. Refresh alternatives.')
        except meal_swaps.StalePreview as exc:
            abort(409,description=str(exc))
        except ValueError as exc:
            abort(400,description=str(exc))
        changed=conn.execute('UPDATE meal_plan_meal SET recipe_snapshot=%s,portion=%s,version=version+1 '
            'WHERE meal_id=%s AND plan_id=%s AND user_id=%s AND version=%s RETURNING meal_id',
            (Jsonb(chosen['recipe']),chosen['portion'],meal_id,plan_id,g.user['user_id'],row['version'])).fetchone()
        if not changed:abort(409,description='This meal changed. Refresh alternatives.')
    flash('Planned meal replaced. Plan totals were recalculated; existing diary entries are unchanged.')
    return redirect(url_for('meals.plan_detail',plan_id=plan_id))


@bp.post('/meal-plans/<int:plan_id>/save')
@login_required
def plan_save(plan_id):
    with connect() as conn:
        lock_limits(conn)
        plan = owned_plan(conn,plan_id,' FOR UPDATE')
        if plan['status']!='draft':
            abort(409,description='This plan is already saved.')
        active,rows,state=limits_state(conn,plan)
        validate_limits_preview(request.form.get('limits_preview',''),state)
        rows = plan_meals(conn,plan_id)
        rules,errors = effective_rules(conn,plan)
        errors = plan_errors(rows,rules,errors)
        errors.extend(current_catalogue_errors(conn,rows,rules))
        if errors:
            abort(409,description='Cannot save: '+' '.join(errors))
        total = totals([entry for row in rows for entry in row['entries']])
        problems=nutrition_limits.errors(total,active)
        if problems:abort(409,description='Cannot save: '+' '.join(problems))
        if (any(active.get(key) is not None for key,_,_ in NUTRIENTS)
                and not exact_match(total,active) and request.form.get('accept_differences')!='yes'):
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
        if row['recipe_snapshot'].get('catalogue_recipe'):
            abort(409,description='This source recipe has mixed ingredient units and no verified serving weight. Log the foods you consumed separately; grams are never inferred.')
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
