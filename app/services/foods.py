"""Shared/private food selection and independently reviewed ingredient metadata."""
import re
from flask import abort, g, request
from app.routes.plans import query

# These are matching labels, not a claim of regulatory or cross-contact verification.
ALLERGENS = ('milk', 'egg', 'peanut', 'tree nuts', 'soy', 'gluten', 'fish', 'crustaceans',
             'molluscs', 'sesame', 'celery', 'mustard', 'lupin', 'sulphites')
ALIASES = {'dairy': 'milk', 'eggs': 'egg', 'peanuts': 'peanut', 'nuts': 'tree nuts',
           'soya': 'soy', 'shellfish': 'crustaceans', 'sulfites': 'sulphites'}
DIETS = ('vegetarian', 'vegan', 'pescatarian')


def tokens(value):
    return sorted({re.sub(r'\s+', ' ', item.strip().casefold())
                   for item in re.split(r'[,;\n]+', value or '') if item.strip()})


def metadata_input():
    allergens = request.form.getlist('allergens')
    if any(value not in ALLERGENS for value in allergens):
        raise ValueError('Choose declared allergens from the listed labels.')
    raw = request.form.get('ingredient_labels', '').strip()
    if len(raw) > 1000 or len(tokens(raw)) > 30:
        raise ValueError('Ingredient labels allow up to 30 labels and 1000 characters.')
    return dict(allergens=sorted(set(allergens)), allergen_complete=request.form.get('allergen_complete') == 'yes',
                ingredient_labels=tokens(raw))


def available_foods(conn, search='', scope='all', preparation=''):
    private = [] if scope == 'library' else query(conn,
        "SELECT *, 'private:'||food_id AS token, 'My foods' AS collection FROM nutrition_food "
        'WHERE user_id=%s AND name ILIKE %s ORDER BY name,food_id', (g.user['user_id'], '%'+search+'%'))
    shared = [] if scope == 'mine' else query(conn,
        "SELECT *, 'catalogue:'||catalogue_id AS token, 'Food library' AS collection FROM food_catalogue "
        "WHERE is_active AND name ILIKE %s AND (%s='' OR preparation=%s) ORDER BY name,catalogue_id",
        ('%'+search+'%', preparation, preparation))
    return sorted(private+shared, key=lambda r: (r['name'].casefold(), r['token']))


def selected_food(conn, token, lock=False):
    match = re.fullmatch(r'(private|catalogue):([0-9]{1,18})', token or '')
    if not match:
        raise ValueError('Choose a food from Food library or My foods.')
    kind, identifier = match.groups()
    if kind == 'private':
        row = query(conn, 'SELECT * FROM nutrition_food WHERE food_id=%s AND user_id=%s'+
                    (' FOR SHARE' if lock else ''), (int(identifier), g.user['user_id']), one=True)
    else:
        row = query(conn, 'SELECT * FROM food_catalogue WHERE catalogue_id=%s AND is_active'+
                    (' FOR SHARE' if lock else ''), (int(identifier),), one=True)
    if not row:
        abort(404)
    row['token'] = token
    return row


def food_provenance(food):
    result = dict(food.get('provenance') or {})
    if 'catalogue_id' in food:
        result.update(source=food['source'], source_id=food['source_id'], dataset=food['dataset_version'],
                      source_url=food['source_url'], description=food['name'], preparation=food['preparation'])
    return result
