"""Validate model IDs and portions; all recipes and totals remain server-owned."""
from decimal import Decimal, InvalidOperation
from . import recipe_index, workout_ai
from .meal_planner import PORTIONS, generate as fit_targets
from .nutrition import MEALS
from .workout_generation import strict_json


def shortlist(eligible, text, targets=None):
    if not eligible:
        raise ValueError('No recipes satisfy your current exclusions. Review the listed constraints; none were relaxed.')
    ranked = recipe_index.rank(eligible,text,targets=targets) if targets is not None else recipe_index.rank(eligible,text)
    return ranked


def schema(rows):
    slots = {meal:dict(anyOf=[dict(type='object',additionalProperties=False,required=['recipe_id','portion'],properties=dict(
        recipe_id=dict(type='string',enum=[row['recipe_id'] for row in rows if meal in row['meals']]),
        portion=dict(type='string',enum=[str(value) for value in PORTIONS]))),dict(type='null')]) if any(meal in row['meals'] for row in rows) else dict(type='null') for meal,_ in MEALS}
    return dict(type='object',additionalProperties=False,required=['meals'],properties=dict(
        meals=dict(type='object',additionalProperties=False,required=list(slots),properties=slots)))


def validate(raw, rows):
    value = strict_json(raw)
    if (not isinstance(value,dict) or set(value)!={'meals'} or not isinstance(value['meals'],dict)
            or set(value['meals'])!=set(dict(MEALS))):
        raise ValueError('AI returned an invalid nutrition draft. Use the manual planner or try again.')
    by_id = {row['recipe_id']:row for row in rows}
    choices = {}
    for meal,item in value['meals'].items():
        if item is None:
            choices[meal]=None
            continue
        if not isinstance(item,dict) or set(item)!={'recipe_id','portion'}:
            raise ValueError('AI returned unsupported nutrition fields.')
        identifier,portion = item['recipe_id'],item['portion']
        if (type(meal) is not str or meal not in dict(MEALS) or meal in choices
                or type(identifier) is not str or identifier not in by_id
                or meal not in by_id[identifier]['meals'] or type(portion) is not str or len(portion)>8):
            raise ValueError('AI selected an ineligible recipe, meal slot or portion.')
        try:amount = Decimal(portion)
        except InvalidOperation:raise ValueError('AI portion is invalid.') from None
        if not amount.is_finite() or amount not in PORTIONS:
            raise ValueError('AI portion must use the supported serving range.')
        choices[meal] = (by_id[identifier],amount)
    return [choices[key] for key,_ in MEALS]


def generate(rows, targets, text, with_notes=False, snacks=None, candidates=None):
    # Ingredients and macros are supplied, but user identity, diary and credentials are not.
    context = dict(request=text,targets=targets,portions=[str(value) for value in PORTIONS],
        eligible_ids_by_meal={meal:[row['recipe_id'] for row in rows if meal in row['meals']] for meal,_ in MEALS},
        eligible_recipes=[dict({key:row[key] for key in ('recipe_id','name','servings','meals','macros','instructions')},
            ingredients=[dict(name=item['food']['name'],quantity=item['quantity'],unit=item['unit'],note=item['note']) for item in row['ingredients']]) for row in rows])
    preferences=validate(workout_ai.nutrition(context,schema(rows)),rows)
    pool=list(candidates if candidates is not None else rows)
    ids={row['recipe_id'] for row in pool}
    pool += [row for row in (snacks or []) if row['recipe_id'] not in ids and 'snack' in row['meals']]
    choices,notes=fit_targets(pool,targets,preferences)
    if choices is None:raise ValueError('No eligible recipe can be planned; exclusions were not relaxed.')
    return (choices,notes) if with_notes else choices
