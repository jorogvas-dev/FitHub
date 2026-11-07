"""Pinned public recipe import and snapshots compatible with FitHub exclusions."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from psycopg.types.json import Jsonb
from app.routes.plans import query
from .meal_planner import exclusion_reasons
from .foods import ALLERGENS

DATA_ROOT = Path(__file__).resolve().parents[2]/'db/data/unitools'
UNITS = frozenset(('g','kg','ml','l','piece','clove','tbsp','tsp','pinch','sprig','slice'))


def validation_errors(row, review):
    """Reject missing measurements; unresolved allergen mappings stay explicit."""
    errors = []
    if type(row.get('baseServings')) is not int or not 1 <= row['baseServings'] <= 100:
        errors.append('Invalid or missing servings.')
    for key in ('prepMinutes','cookMinutes'):
        if type(row.get(key)) is not int or row[key] < 0:
            errors.append('Invalid or missing '+key+'.')
    for key in ('calories','protein','carbs','fat'):
        value = (row.get('nutritionPerServing') or {}).get(key)
        try:
            if isinstance(value,bool) or value is None or not Decimal(str(value)).is_finite() or not 0 <= Decimal(str(value)) <= 10000:
                raise ValueError
        except (ValueError,ArithmeticError):errors.append('Invalid or missing nutrient: '+key+'.')
    if not row.get('ingredients'):errors.append('Missing ingredients.')
    for item in row.get('ingredients',[]):
        identifier = item.get('id','<missing>')
        quantity = item.get('quantity')
        try:
            if isinstance(quantity,bool) or quantity is None or not Decimal(str(quantity)).is_finite() or Decimal(str(quantity)) <= 0:
                raise ValueError
        except (ValueError,ArithmeticError):errors.append(identifier+': missing or invalid positive quantity.')
        if item.get('unit') not in UNITS:errors.append(identifier+': unsupported measurement unit.')
        if item.get('scaling') not in ('linear','damped','fixed'):errors.append(identifier+': unsupported scaling rule.')
        name = (item.get('name') or {}).get('en','').strip()
        declaration = review.get(identifier)
        if not name or not declaration or name not in declaration.get('names',[]):
            errors.append(identifier+': unresolved ingredient identity mapping.')
        elif (not isinstance(declaration.get('allergens'),list) or set(declaration['allergens'])-set(ALLERGENS)
              or not isinstance(declaration.get('labels'),list)
              or any(not isinstance(label,str) or not label.strip() for label in declaration['labels'])):
            errors.append(identifier+': invalid ingredient mapping declaration.')
    if not row.get('steps') or any(not (step.get('text') or {}).get('en','').strip() for step in row.get('steps',[])):
        errors.append('Missing preparation instructions.')
    return errors


def source_audit(root=None):
    root = Path(root or DATA_ROOT)
    source = json.loads((root/'unitools-recipes-v1.json').read_text(encoding='utf-8'),parse_float=Decimal)
    review = json.loads((root/'ingredient-review.json').read_text(encoding='utf-8'))
    return [dict(source_id=row['slug'],name=row['name']['en'],reasons=validation_errors(row,review)) for row in source['recipes']]


def load_pilot(root=None):
    root = Path(root or DATA_ROOT)
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    for filename, checksum in [('unitools-recipes-v1.json',manifest['sha256']),('LICENSE',manifest['license_sha256'])]:
        if hashlib.sha256((root/filename).read_bytes()).hexdigest() != checksum:
            raise ValueError('Pinned recipe data or license checksum does not match.')
    source = json.loads((root/'unitools-recipes-v1.json').read_text(encoding='utf-8'),parse_float=Decimal)
    review = json.loads((root/'ingredient-review.json').read_text(encoding='utf-8'))
    by_slug = {row['slug']:row for row in source['recipes']}
    if not 1 <= len(manifest['recipes']) <= 100 or len(set(manifest['recipes'])) != len(manifest['recipes']):
        raise ValueError('Collection must contain 1–100 unique recipes.')
    rows = []
    for slug in manifest['recipes']:
        row = by_slug[slug]
        errors = validation_errors(row,review)
        if errors:raise ValueError(slug+': '+' '.join(errors))
        servings = row['baseServings']
        if type(servings) is not int or not 1 <= servings <= 100:
            raise ValueError('Invalid source servings.')
        if any(type(row.get(key)) is not int or row[key]<0 for key in ('prepMinutes','cookMinutes')):
            raise ValueError('Invalid source preparation time.')
        macros = {key:str(row['nutritionPerServing'][field]) for key,field in [('kcal','calories'),('protein','protein'),('carbs','carbs'),('fat','fat')]}
        if any(not Decimal(value).is_finite() or not 0 <= Decimal(value) <= 10000 for value in macros.values()):
            raise ValueError('Invalid source nutrition.')
        ingredients = []
        for item in row['ingredients']:
            name, note = item['name']['en'], (item.get('note') or {}).get('en','')
            declaration = review.get(item['id'])
            # Notes may include substitutes: only explicitly reviewed exact names
            # and preparation annotations receive complete declarations.
            complete = bool(declaration and declaration.get('reviewed',True) and name in declaration['names']
                            and name not in declaration.get('unreviewed_names',[])
                            and (not note or note in declaration.get('notes',[])))
            allergens = declaration['allergens'] if declaration else []
            labels = {item['id'].casefold(), name.casefold(), *[value.casefold() for value in (declaration or {}).get('labels',[])]}
            words = re.findall(r'[a-z]+', name.casefold()+' '+note.casefold())
            labels.update(' '.join(words[i:i+n]) for n in range(1,5) for i in range(len(words)-n+1))
            labels.update(allergens)
            quantity = None if item['quantity'] is None else str(item['quantity'])
            if quantity is not None and (not Decimal(quantity).is_finite() or Decimal(quantity)<0):
                raise ValueError('Invalid ingredient quantity.')
            ingredients.append(dict(source_id=item['id'],quantity=quantity,unit=item['unit'],scaling=item['scaling'],note=note,
                food=dict(name=name,source_allergens=allergens),allergens=allergens,allergen_complete=complete,ingredient_labels=sorted(labels)))
        if not ingredients or not row['steps']:
            raise ValueError('Recipe ingredients and instructions are required.')
        meals = ['breakfast'] if row['category']=='breakfast' else ['snack'] if row['category'] in ('snack','salad','dessert','bread','drink','sauce') else ['lunch','dinner']
        tags = ['public recipe',row['category'],row['country']]
        if Decimal(macros['protein'])>=20:tags.append('higher protein')
        if row['category']=='breakfast' and row['prepMinutes']+row['cookMinutes']<=30:tags.append('quick breakfast')
        if servings>=4:tags.append('meal prep')
        if row['prepMinutes']+row['cookMinutes']<=30:tags.append('quick meal')
        if Decimal(macros['protein'])>=20 and Decimal(macros['fat'])<=10:tags.append('low-fat protein')
        diets = [value for value in row['diets'] if value in ('vegan','vegetarian','pescatarian')]
        if 'vegan' in diets:diets=['vegan','vegetarian','pescatarian']
        elif 'vegetarian' in diets:diets=['vegetarian','pescatarian']
        record = dict(recipe_id='unitools:'+slug,source='unitools',source_id=slug,name=row['name']['en'],description=row['summary']['en'],servings=servings,
            meals=meals,dietary_tags=diets,tags=tags,ingredients=ingredients,
            prep_minutes=row['prepMinutes'],cook_minutes=row['cookMinutes'],
            instructions=[dict(text=step['text']['en'],minutes=step['minutes']) for step in row['steps']],macros=macros,
            provenance={**{key:value for key,value in manifest.items() if key not in ('recipes','retired_recipes')},'dataset_version':source['version']})
        record['content_sha256']=hashlib.sha256(json.dumps(record,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        rows.append(record)
    return rows


def import_pilot(conn, dry_run=False):
    rows = load_pilot()
    existing = dict(conn.execute('SELECT recipe_id,content_sha256 FROM recipe_catalogue'))
    result = dict(recipes=len(rows),new=sum(row['recipe_id'] not in existing for row in rows),
                  changed=sum(row['recipe_id'] in existing and existing[row['recipe_id']]!=row['content_sha256'] for row in rows),
                  incomplete_ingredients=sum(not item['allergen_complete'] for row in rows for item in row['ingredients']),dry_run=dry_run)
    if not dry_run:
        conn.execute('SELECT pg_advisory_xact_lock(%s)',(734104022,))
        # Retain source rows and historical snapshots; retire only reviewed legacy
        # pilot IDs explicitly listed in this versioned manifest. Never reactivate.
        manifest=json.loads((DATA_ROOT/'manifest.json').read_text(encoding='utf-8'))
        retired=['unitools:'+slug for slug in manifest.get('retired_recipes',[])]
        conn.execute('UPDATE recipe_catalogue SET is_active=false WHERE recipe_id=ANY(%s)',(retired,))
        columns = ('recipe_id','source','source_id','name','description','servings','meals','dietary_tags','tags','ingredients','instructions','macros','provenance','content_sha256','prep_minutes','cook_minutes')
        for row in rows:
            values = [Jsonb(row[key]) if key in ('ingredients','instructions','macros','provenance') else row[key] for key in columns]
            conn.execute('INSERT INTO recipe_catalogue('+','.join(columns)+') VALUES ('+','.join(['%s']*len(columns))+') '
                         'ON CONFLICT(recipe_id) DO UPDATE SET '+','.join(key+'=EXCLUDED.'+key for key in columns[1:])+
                         ' WHERE recipe_catalogue.content_sha256<>EXCLUDED.content_sha256',values)
    result['retired_source_ids']=json.loads((DATA_ROOT/'manifest.json').read_text(encoding='utf-8')).get('retired_recipes',[])
    return result


def snapshot(row):
    return dict(recipe_id=row['recipe_id'],catalogue_recipe=True,name=row['name'],servings=row['servings'],meals=row['meals'],dietary_tags=row['dietary_tags'],
        allergens=sorted({value for item in row['ingredients'] for value in item['allergens']}),
        allergen_complete=all(item['allergen_complete'] for item in row['ingredients']),ingredients=row['ingredients'],
        macros=row['macros'],instructions=' '.join(step['text'] for step in row['instructions']),provenance=row['provenance'],
        description=row['description'],tags=row['tags'],prep_minutes=row.get('prep_minutes'),cook_minutes=row.get('cook_minutes'))


def eligible(conn, rules, require_nutrition=True):
    allowed, excluded = [], []
    for row in query(conn,'SELECT * FROM recipe_catalogue WHERE is_active ORDER BY recipe_id'):
        item = snapshot(row)
        reasons = exclusion_reasons(item,rules,require_nutrition=require_nutrition)
        if reasons:excluded.append(dict(name=row['name'],reasons=reasons))
        else:allowed.append(item)
    return allowed, excluded
