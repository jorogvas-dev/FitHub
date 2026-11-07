"""Decimal recipe snapshots, explicit exclusions and a bounded deterministic search."""
from decimal import Decimal, ROUND_HALF_UP
from app.services.foods import ALIASES, ALLERGENS, DIETS, food_provenance, tokens
from app.services.nutrition import CENT, MEALS, NUTRIENTS, amount, entry_totals

D = Decimal
PORTIONS = tuple(D(i)/4 for i in range(1,9))
MAX_RECIPES_PER_MEAL = 20
BEAM_WIDTH = 128


def food_snapshot(food):
    return dict(name=food['name'], provenance=food_provenance(food),
                source_allergens=food.get('allergens',[]),
                **{key+'_100g': None if food[key+'_100g'] is None else str(food[key+'_100g'])
                   for key, _, _ in NUTRIENTS})


def recipe_snapshot(recipe, ingredients):
    return dict(name=recipe['name'], recipe_id=recipe['recipe_id'], servings=recipe['servings'],
                meals=recipe['meals'], dietary_tags=recipe['dietary_tags'],
                allergens=recipe['allergens'], allergen_complete=recipe['allergen_complete'],
                ingredients=[dict(food=row['food_snapshot'], grams=str(row['grams']),
                                  allergens=row['allergens'], allergen_complete=row['allergen_complete'],
                                  ingredient_labels=row['ingredient_labels']) for row in ingredients])


def portion_entries(snapshot, portion):
    """Exact Decimal quantities from stored batch grams and serving count."""
    if portion not in PORTIONS:
        raise ValueError('Choose 0.25–2 servings in steps of 0.25.')
    if snapshot.get('catalogue_recipe'):
        # Source recipes have mixed units, not a known serving mass. Never invent
        # grams or per-100g nutrient rates from per-serving source nutrition.
        return [dict(recipe_macros=snapshot['macros'],portion=portion)]
    entries = []
    for row in snapshot['ingredients']:
        grams = D(row['grams'])*portion/D(snapshot['servings'])
        if not CENT <= grams <= 10000:
            raise ValueError('This portion gives an ingredient below 0.01 g or above 10000 g.')
        entries.append(dict(food_name=row['food']['name'], grams=grams,
                            provenance=row['food']['provenance'],
                            **{key+'_100g': None if row['food'][key+'_100g'] is None else D(row['food'][key+'_100g'])
                               for key, _, _ in NUTRIENTS}))
    return entries


def totals(entries):
    values = [{key:None if row['recipe_macros'].get(key) is None else D(row['recipe_macros'][key])*row['portion']
               for key,_,_ in NUTRIENTS} if 'recipe_macros' in row else entry_totals(row) for row in entries]
    return {key: None if any(row[key] is None for row in values) else sum((row[key] for row in values), D(0))
            for key, _, _ in NUTRIENTS}


def restrictions(settings):
    expanded = {'nuts': ('tree nuts','peanut'), 'shellfish': ('crustaceans','molluscs')}
    allergies = [label for item in tokens(settings['allergies'])
                 for label in expanded.get(item,(ALIASES.get(item,item),))]
    diets = tokens(settings['dietary_preferences'])
    errors = []
    unknown = sorted(set(allergies)-set(ALLERGENS))
    if unknown:
        errors.append('Unrecognised allergy labels: '+', '.join(unknown)+'. Use the documented labels; exclusions were not relaxed.')
    unknown = sorted(set(diets)-set(DIETS))
    if unknown:
        errors.append('Unrecognised dietary preferences: '+', '.join(unknown)+'. Supported tags: '+', '.join(DIETS)+'.')
    unwanted = tokens(settings['unwanted_ingredients'])
    # Legacy prose must be clarified instead of being silently ignored.
    if any(any(word in item.split() for word in ('no','avoid','without','except','and','or')) for item in unwanted):
        errors.append('Use comma-separated unwanted ingredient labels, without prose such as “avoid” or “no”.')
    return dict(allergies=sorted(set(allergies)), unwanted=unwanted, diets=diets), errors


def exclusion_reasons(snapshot, rules, require_nutrition=True):
    reasons = []
    if not snapshot['ingredients']:
        return ['No ingredients.']
    if rules['allergies']:
        reviewed = snapshot['allergen_complete'] and all(row['allergen_complete'] for row in snapshot['ingredients'])
        if not reviewed:
            reasons.append('Incomplete recipe or ingredient allergen metadata.')
        declared = set(snapshot['allergens'])
        for row in snapshot['ingredients']:
            declared.update(row['allergens'])
            declared.update(row['food'].get('source_allergens',[]))
        conflicts = declared.intersection(rules['allergies'])
        if conflicts:
            reasons.append('Declared allergens: '+', '.join(sorted(conflicts))+'.')
    if rules['unwanted']:
        if any(not row['ingredient_labels'] for row in snapshot['ingredients']):
            reasons.append('Ingredient labels are incomplete for unwanted-ingredient matching.')
        labels = {label for row in snapshot['ingredients'] for label in row['ingredient_labels']}
        matches = labels.intersection(rules['unwanted'])
        if matches:
            reasons.append('Unwanted ingredients: '+', '.join(sorted(matches))+'.')
    if not set(rules['diets']).issubset(snapshot['dietary_tags']):
        reasons.append('Missing required dietary preference tags.')
    if require_nutrition and snapshot.get('catalogue_recipe'):
        if any(snapshot['macros'].get(key) is None for key,_,_ in NUTRIENTS):
            reasons.append('Unknown per-serving nutrition values.')
    elif require_nutrition and any(row['food'][key+'_100g'] is None
                                 for row in snapshot['ingredients'] for key, _, _ in NUTRIENTS):
        reasons.append('Unknown nutrition values; automatic planning requires all four nutrients.')
    return reasons


def differences(total, targets):
    return {key: None if targets.get(key) is None or total[key] is None else total[key]-D(targets[key])
            for key, _, _ in NUTRIENTS}


def exact_match(total, targets):
    selected=[key for key,_,_ in NUTRIENTS if targets.get(key) is not None]
    return bool(selected) and all(total[key] is not None and total[key] == D(targets[key]) for key in selected)


def generate(snapshots, targets, preferences=None):
    from .nutrition_fit import fit
    return fit(snapshots,targets,preferences)
