"""Reviewed plain-food recipe templates; nutrients always come from the active library.

Source identities/preparations are verified against the bundled USDA SR Legacy subset.
Allergen declarations describe these ingredients, not cross-contact or packaged variants.
"""
from functools import lru_cache
import json
from pathlib import Path

from app.routes.plans import query
from .meal_planner import food_snapshot, exclusion_reasons

# source ID: (matching ingredient labels, declared supported allergens)
INGREDIENTS = {
    '169705': (['oats','oat','oatmeal'], ['gluten']),
    '173944': (['banana','bananas'], []),
    '170894': (['yogurt','yoghurt','greek yogurt','milk','dairy'], ['milk']),
    '167762': (['strawberry','strawberries','berries'], []),
    '173757': (['chickpea','chickpeas','garbanzo beans'], []),
    '169704': (['rice','brown rice'], []),
    '168462': (['spinach'], []),
    '171413': (['oil','olive oil','olives'], []),
    '171477': (['chicken','chicken breast','meat'], []),
    '172421': (['lentil','lentils'], []),
    '168483': (['sweet potato','sweet potatoes'], []),
    '172475': (['tofu','soy','soya'], ['soy']),
    '168409': (['cucumber','cucumbers'], []),
    '171688': (['apple','apples'], []),
    '172470': (['peanut butter','peanut','peanuts','nuts'], ['peanut']),
}
VEGAN = ['vegan','vegetarian','pescatarian']
# Stable recipe keys are independent of account-owned recipe IDs. Grams match the
# listed food preparation (cooked rice/legumes/meat, dry oats, raw fruit/vegetables).
RECIPES = (
    ('banana-oats','Banana oats',['breakfast'],VEGAN,
     [('169705','50'),('173944','120')], 'Weigh oats dry, then prepare with water. Add the weighed banana.'),
    ('yogurt-berries','Yogurt and berries',['breakfast'],['vegetarian','pescatarian'],
     [('170894','170'),('167762','100')], 'Combine plain nonfat Greek yogurt and raw strawberries.'),
    ('chickpea-rice','Chickpea rice bowl',['lunch'],VEGAN,
     [('173757','150'),('169704','150'),('168462','50'),('171413','5')], 'Use cooked chickpeas and cooked rice; add spinach and olive oil.'),
    ('chicken-rice','Chicken rice bowl',['lunch'],[],
     [('171477','120'),('169704','150'),('168462','50'),('171413','5')], 'Weigh roasted cooked chicken and cooked rice. Add spinach and olive oil.'),
    ('lentil-sweet-potato','Lentils and sweet potato',['dinner'],VEGAN,
     [('172421','180'),('168483','200'),('168462','50'),('171413','5')], 'Use cooked lentils and baked sweet-potato flesh; add spinach and olive oil.'),
    ('tofu-rice','Tofu rice bowl',['dinner'],VEGAN,
     [('172475','150'),('169704','150'),('168409','100'),('171413','5')], 'Weigh firm tofu before preparation and rice after cooking; add cucumber and olive oil.'),
    ('apple-peanut','Apple and peanut butter',['snack'],VEGAN,
     [('171688','150'),('172470','20')], 'Use raw apple with skin and plain unsalted smooth peanut butter.'),
    ('banana-berries','Banana and berries',['snack'],VEGAN,
     [('173944','120'),('167762','100')], 'Combine weighed raw banana and strawberries.'),
)


@lru_cache(maxsize=1)
def verified_foods():
    path=Path(__file__).resolve().parents[2]/'db/data/usda-sr-2018.json'
    data=json.loads(path.read_text(encoding='utf-8'))
    return data['source'], {row['source_id']:row for row in data['foods']}


def candidates(conn, rules, require_nutrition=True):
    source, verified=verified_foods()
    rows=query(conn,'SELECT * FROM food_catalogue WHERE source=%s AND source_id=ANY(%s) FOR SHARE',
               (source,list(INGREDIENTS)))
    foods={row['source_id']:row for row in rows}
    eligible,excluded=[],[]
    for key,name,meals,diets,items,instructions in RECIPES:
        children=[]
        reasons=[]
        declared=set()
        for source_id,grams in items:
            food=foods.get(source_id)
            expected=verified[source_id]
            if not food or not food['is_active']:
                reasons.append('Ingredient unavailable or deactivated: '+expected['name']+'.')
                continue
            if (food['name'],food['preparation']) != (expected['name'],expected['preparation']):
                reasons.append('Ingredient description or preparation changed; review required: '+expected['name']+'.')
                continue
            labels,allergens=INGREDIENTS[source_id]
            if set(food.get('allergens',[]))-set(allergens):
                reasons.append('Ingredient allergen declarations changed; review required: '+expected['name']+'.')
                continue
            declared.update(allergens)
            children.append(dict(food=food_snapshot(food),grams=grams,allergens=list(allergens),
                allergen_complete=True,ingredient_labels=sorted(set(labels)|set(food.get('ingredient_labels',[])))))
        snapshot=dict(recipe_id='starter:'+key,name=name,servings=1,meals=list(meals),dietary_tags=list(diets),
                      allergens=sorted(declared),allergen_complete=True,ingredients=children,
                      starter=True,instructions=instructions)
        if not reasons:
            reasons=exclusion_reasons(snapshot,rules,require_nutrition=require_nutrition)
        if reasons:
            excluded.append(dict(name=name,reasons=list(dict.fromkeys(reasons))))
        else:
            eligible.append(snapshot)
    return eligible,excluded
