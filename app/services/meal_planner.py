"""Decimal recipe snapshots, explicit exclusions and a bounded deterministic search."""
from decimal import Decimal, ROUND_HALF_UP
from app.services.foods import ALIASES, ALLERGENS, DIETS, food_provenance, tokens
from app.services.nutrition import CENT, MEALS, NUTRIENTS, entry_totals

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
    """Round physical gram quantities once; planned and logged quantities are identical."""
    if portion not in PORTIONS:
        raise ValueError('Choose 0.25–2 servings in steps of 0.25.')
    entries = []
    for row in snapshot['ingredients']:
        grams = (D(row['grams'])*portion/D(snapshot['servings'])).quantize(CENT, rounding=ROUND_HALF_UP)
        if not CENT <= grams <= 10000:
            raise ValueError('This portion gives an ingredient below 0.01 g or above 10000 g.')
        entries.append(dict(food_name=row['food']['name'], grams=grams,
                            provenance=row['food']['provenance'],
                            **{key+'_100g': None if row['food'][key+'_100g'] is None else D(row['food'][key+'_100g'])
                               for key, _, _ in NUTRIENTS}))
    return entries


def totals(entries):
    values = [entry_totals(row) for row in entries]
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
    if require_nutrition and any(row['food'][key+'_100g'] is None
                                 for row in snapshot['ingredients'] for key, _, _ in NUTRIENTS):
        reasons.append('Unknown nutrition values; automatic planning requires all four nutrients.')
    return reasons


def differences(total, targets):
    return {key: total[key]-D(targets[key]) for key, _, _ in NUTRIENTS}


def exact_match(total, targets):
    return all(total[key] is not None and total[key] == D(targets[key]) for key, _, _ in NUTRIENTS)


def generate(snapshots, targets):
    """Search a documented finite grid; never describe a heuristic result as an optimum."""
    if any(targets.get(key) is None for key, _, _ in NUTRIENTS):
        return None, ['Set all four manual daily targets before generating a draft.']
    target = {key: D(targets[key]) for key, _, _ in NUTRIENTS}
    choices = []
    notes = []
    for meal, label in MEALS:
        eligible = [row for row in snapshots if meal in row['meals']]
        if len(eligible) > MAX_RECIPES_PER_MEAL:
            notes.append(f'{label}: search limited to the first {MAX_RECIPES_PER_MEAL} eligible recipes by name / ID.')
        options = []
        for snapshot in eligible[:MAX_RECIPES_PER_MEAL]:
            for portion in PORTIONS:
                try:
                    value = totals(portion_entries(snapshot, portion))
                except ValueError:
                    continue
                if all(value[key] is not None for key, _, _ in NUTRIENTS):
                    options.append((snapshot, portion, value))
        if not options:
            notes.append(f'No eligible {label.lower()} recipe with complete nutrients and valid gram quantities at the supported portions.')
            return None, notes
        choices.append(options)
    for key, label, unit in NUTRIENTS:
        low = sum((min(option[2][key] for option in group) for group in choices), D(0))
        high = sum((max(option[2][key] for option in group) for group in choices), D(0))
        if not low <= target[key] <= high:
            notes.append(f'{label} target is outside the searched portion range ({low:.2f}–{high:.2f} {unit}). Targets cannot all be met within this grid.')
    # Keep bounded prefixes close to progressive daily targets. All arithmetic stays Decimal.
    beam = [(D(0), (), {key: D(0) for key, _, _ in NUTRIENTS})]
    fractions = (D('.25'), D('.60'), D('.90'), D(1))
    for index, options in enumerate(choices):
        expanded = []
        for _, selected, current in beam:
            for option_index, (_, _, value) in enumerate(options):
                total = {key: current[key]+value[key] for key, _, _ in NUTRIENTS}
                score = sum(((total[key]-target[key]*fractions[index])/max(target[key],D(1)))**2
                            for key, _, _ in NUTRIENTS)
                expanded.append((score, selected+(option_index,), total))
        expanded.sort(key=lambda row: (row[0], row[1]))
        beam = expanded[:BEAM_WIDTH]
    _, selected, result = beam[0]
    notes.append('Bounded search: up to 20 recipes per meal, 0.25–2 servings in 0.25 steps, 128 retained combinations per stage. Recipes may repeat. Global optimality is not guaranteed.')
    notes.append('All four targets matched exactly.' if exact_match(result, target) else
                 'Targets were not met. Review the explicit differences; this is the closest draft found in the bounded search.')
    return [(choices[i][chosen][0],choices[i][chosen][1]) for i,chosen in enumerate(selected)], notes
