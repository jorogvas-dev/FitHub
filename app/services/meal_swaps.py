"""Deterministic, portion-aware alternatives with signed, expiring confirmations."""
from decimal import Decimal, InvalidOperation
import hashlib
import json

from flask import current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from . import recipe_catalogue, workout_ai, nutrition_limits
from .meal_planner import PORTIONS, portion_entries, totals
from .nutrition import NUTRIENTS
from .workout_generation import strict_json

GOALS = {'similar':'Similar calories','protein':'More protein','quicker':'Quicker to prepare'}
EXPLANATIONS = {
    'calorie_match':'This portion stays within the calorie comparison range.',
    'protein_gain':'This portion provides more protein than the current meal.',
    'time_saved':'The original recipe batch takes less preparation and cooking time.',
}
PREVIEW_SECONDS = 1800


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def serializer():
    return URLSafeTimedSerializer(current_app.secret_key,salt='fithub-meal-swap-v1')


def time_minutes(recipe):
    values=[recipe.get('prep_minutes'),recipe.get('cook_minutes')]
    return sum(values) if all(type(value) is int and value>=0 for value in values) else None


def current_time(conn,snapshot):
    known=time_minutes(snapshot)
    if known is not None:return known
    # Older snapshots can use preparation data from the exact same pinned revision.
    if not snapshot.get('catalogue_recipe'):return None
    row=conn.execute('SELECT prep_minutes,cook_minutes,provenance FROM recipe_catalogue WHERE recipe_id=%s',
                     (snapshot.get('recipe_id'),)).fetchone()
    if row and snapshot.get('provenance',{}).get('commit') and snapshot['provenance']['commit']==row[2].get('commit'):
        return row[0]+row[1] if all(type(value) is int and value>=0 for value in row[:2]) else None
    return None


def alternatives(conn,meal,rules,goal,targets=None,other_totals=None):
    if goal not in GOALS:raise ValueError('Choose a supported swap comparison.')
    current=totals(portion_entries(meal['recipe_snapshot'],meal['portion']))
    minutes=current_time(conn,meal['recipe_snapshot'])
    baseline=dict(totals=current,minutes=minutes,portion=meal['portion'],name=meal['recipe_snapshot']['name'])
    if current['kcal'] is None:
        return baseline,[],'Current meal calories are unknown. A calorie comparison is unavailable.'
    if goal=='protein' and current['protein'] is None:
        return baseline,[],'Current meal protein is unknown. Choose another comparison.'
    if goal=='quicker' and minutes is None:
        return baseline,[],'Preparation time is unavailable for the current recipe. Choose another comparison.'
    tolerance=max(Decimal(50),current['kcal']*Decimal('.15'))
    baseline['tolerance']=tolerance
    eligible,excluded=recipe_catalogue.eligible(conn,rules,require_nutrition=False)
    results=[]
    for recipe in eligible:
        if recipe['recipe_id']==meal['recipe_snapshot'].get('recipe_id') or meal['meal'] not in recipe['meals']:
            continue
        candidate_minutes=time_minutes(recipe)
        if goal=='quicker' and (candidate_minutes is None or candidate_minutes>=minutes):continue
        variants=[]
        for portion in PORTIONS:
            values=totals(portion_entries(recipe,portion))
            if targets is not None and nutrition_limits.errors(nutrition_limits.combine(other_totals,values),targets):continue
            if values['kcal'] is None or abs(values['kcal']-current['kcal'])>tolerance:continue
            if goal=='protein' and (values['protein'] is None or values['protein']<=current['protein']):continue
            difference={key:values[key]-current[key] if values[key] is not None and current[key] is not None else None for key,_,_ in NUTRIENTS}
            reason_ids=['calorie_match']
            if difference['protein'] is not None and difference['protein']>0:reason_ids.append('protein_gain')
            if candidate_minutes is not None and minutes is not None and candidate_minutes<minutes:reason_ids.append('time_saved')
            primary=(-difference['protein'] if goal=='protein' else candidate_minutes if goal=='quicker' else abs(difference['kcal']))
            score=(primary,abs(difference['kcal']),abs(portion-meal['portion']),portion,recipe['recipe_id'])
            variants.append(dict(recipe=recipe,portion=portion,totals=values,difference=difference,minutes=candidate_minutes,
                                 time_difference=candidate_minutes-minutes if candidate_minutes is not None and minutes is not None else None,
                                 reason_ids=reason_ids,score=score))
        if variants:results.append(min(variants,key=lambda option:option['score']))
    results.sort(key=lambda option:option['score'])
    note='No suitable alternatives meet this comparison and your current exclusions. Try another comparison or review your preferences.'
    return baseline,results[:3],note if not results else None


def token_for(user_id,plan,meal,option,goal,context=None):
    return serializer().dumps(dict(user_id=user_id,plan_id=plan['plan_id'],meal_id=meal['meal_id'],
        version=meal['version'],current=fingerprint(dict(snapshot=meal['recipe_snapshot'],portion=str(meal['portion']))),
        recipe_id=option['recipe']['recipe_id'],portion=str(option['portion']),candidate=fingerprint(option['recipe']),goal=goal,context=context))


class StalePreview(ValueError):
    pass


def validate_token(token,user_id,plan,meal,context=None):
    try:
        if not isinstance(token,str) or len(token)>4096:raise BadSignature('Invalid preview.')
        value=serializer().loads(token,max_age=PREVIEW_SECONDS)
    except SignatureExpired:
        raise StalePreview('This swap preview expired. Refresh alternatives before replacing the meal.') from None
    except BadSignature:
        raise ValueError('Invalid swap preview. Refresh alternatives.') from None
    if not isinstance(value,dict) or any(value.get(key)!=expected for key,expected in
            [('user_id',user_id),('plan_id',plan['plan_id']),('meal_id',meal['meal_id'])]):
        raise ValueError('This preview does not belong to the selected meal.')
    digest=fingerprint(dict(snapshot=meal['recipe_snapshot'],portion=str(meal['portion'])))
    if value.get('version')!=meal['version'] or value.get('current')!=digest:
        raise StalePreview('This meal changed or was already replaced. Refresh alternatives before trying again.')
    if value.get('context')!=context:raise StalePreview('The plan, targets or exclusions changed. Refresh alternatives.')
    if value.get('goal') not in GOALS or type(value.get('recipe_id')) is not str or type(value.get('portion')) is not str:
        raise ValueError('Invalid swap preview.')
    try:portion=Decimal(value['portion'])
    except InvalidOperation:raise ValueError('Invalid swap portion.') from None
    if not portion.is_finite() or portion not in PORTIONS:raise ValueError('Invalid swap portion.')
    return value


def explain(baseline,options):
    """Optional Coach emphasis; no model-supplied text or values are rendered."""
    rows=[dict(recipe_id=option['recipe']['recipe_id'],portion=str(option['portion']),
               totals={key:str(value) if value is not None else None for key,value in option['totals'].items()},
               differences={key:str(value) if value is not None else None for key,value in option['difference'].items()},
               minutes=option['minutes'],reason_ids=option['reason_ids']) for option in options]
    schema=dict(type='object',additionalProperties=False,required=['explanations'],properties=dict(
        explanations=dict(type='array',maxItems=3,items=dict(type='object',additionalProperties=False,required=['recipe_id','reason_id'],properties=dict(
            recipe_id=dict(type='string',enum=[row['recipe_id'] for row in rows]),reason_id=dict(type='string',enum=list(EXPLANATIONS)))))))
    try:
        value=strict_json(workout_ai.explain_meal_swaps(dict(alternatives=rows),schema))
        if not isinstance(value,dict) or set(value)!={'explanations'} or not isinstance(value['explanations'],list) or len(value['explanations'])>3:raise ValueError
        allowed={row['recipe_id']:row['reason_ids'] for row in rows}
        result={}
        for item in value['explanations']:
            if not isinstance(item,dict) or set(item)!={'recipe_id','reason_id'}:raise ValueError
            identifier,reason=item['recipe_id'],item['reason_id']
            if type(identifier) is not str or identifier not in allowed or identifier in result or reason not in allowed[identifier]:raise ValueError
            result[identifier]=EXPLANATIONS[reason]
        return result,None
    except (ValueError,TypeError,KeyError,RecursionError):
        return {},'Coach explanation is unavailable. The alternatives are ready to use.'
