"""Measured public collection and bounded selection across the entire pool."""
import copy
from decimal import Decimal as D
import json
import unittest
from unittest.mock import patch
from app.services import recipe_catalogue as catalog, nutrition_fit, nutrition_ai, recipe_index
from app.services.meal_planner import exclusion_reasons, totals, portion_entries


class RecipeExpansionTests(unittest.TestCase):
    def test_source_audit_complete_measurements_and_explicit_unknown_mappings(self):
        rows=catalog.load_pilot()
        self.assertEqual(len(rows),100)
        self.assertEqual(len({r['recipe_id'] for r in rows}),100)
        self.assertTrue(all(i['quantity'] is not None and D(i['quantity'])>0 and i['unit'] in catalog.UNITS for r in rows for i in r['ingredients']))
        self.assertTrue(all(set(r['macros'])=={'kcal','protein','carbs','fat'} for r in rows))
        self.assertTrue(all(r['provenance']['license']=='CC BY-SA 4.0' for r in rows))
        audit=catalog.source_audit()
        self.assertEqual(len(audit),501)
        self.assertEqual(sum(bool(r['reasons']) for r in audit),379)
        unknown=next(catalog.snapshot(r) for r in rows if any(not i['allergen_complete'] for i in r['ingredients']))
        self.assertTrue(exclusion_reasons(unknown,dict(allergies=['milk'],unwanted=[],diets=[])))

    def test_invalid_units_values_identity_servings_and_instructions_rejected(self):
        root=catalog.DATA_ROOT
        source=json.loads((root/'unitools-recipes-v1.json').read_text(encoding='utf-8'))
        review=json.loads((root/'ingredient-review.json').read_text(encoding='utf-8'))
        good=next(r for r in source['recipes'] if r['slug']=='skyr-with-berries')
        self.assertEqual(catalog.validation_errors(good,review),[])
        for quantity in (None,'NaN','Infinity',-1,True,0):
            bad=copy.deepcopy(good);bad['ingredients'][0]['quantity']=quantity
            self.assertTrue(catalog.validation_errors(bad,review))
        for mutation in (lambda r:r['ingredients'][0].update(unit='bucket'),
                         lambda r:r['ingredients'][0].update(id='unmapped'),
                         lambda r:r['nutritionPerServing'].update(protein=None),
                         lambda r:r['nutritionPerServing'].update(fat='NaN'),
                         lambda r:r.update(baseServings=True),lambda r:r.update(steps=[]),
                         lambda r:r['steps'][0].update(text={'en':''})):
            bad=copy.deepcopy(good);mutation(bad)
            self.assertTrue(catalog.validation_errors(bad,review))

    def test_full_pool_considered_before_twenty_candidate_cap_and_exact_limits(self):
        from test_nutrition_limits import recipe
        rows=[recipe('unsuitable'+str(i),['breakfast'],protein='0',fat='100') for i in range(30)]
        rows.append(recipe('newly-imported',['breakfast'],kcal='100.004',protein='10',fat='0'))
        targets=dict(kcal='100',protein='10',fat='0')
        choices,_=nutrition_fit.fit(rows,targets)
        self.assertEqual(choices[0][0]['recipe_id'],'newly-imported')
        self.assertEqual(choices[0][1],D('.75'))
        self.assertEqual(totals(portion_entries(*choices[0]))['kcal'],D('75.003'))
        self.assertEqual(nutrition_fit.MAX_RECIPES_PER_SLOT,20)
        self.assertEqual(nutrition_fit.BEAM_WIDTH,128)

    def test_each_slot_query_covers_expansion_and_shortlist_stays_bounded(self):
        rows=[catalog.snapshot(r) for r in catalog.load_pilot()]
        class Response:
            def __init__(self,value):self.value=value
            def raise_for_status(self):pass
            def json(self):return self.value
        payloads=[]
        def respond(path,**kwargs):
            payload=kwargs['json'];payloads.append(payload)
            return Response(dict(ids=[payload['where']['recipe_id']['$in'][-3:]]))
        with patch('app.services.recipe_index.httpx.Client') as client:
            connection=client.return_value.__enter__.return_value
            connection.get.return_value=Response(dict(id='test',metadata=recipe_index.METADATA))
            connection.post.side_effect=respond
            result=nutrition_ai.shortlist(rows,'quick protein',dict(kcal='2000',protein='140',fat='60'))
        self.assertLessEqual(len(result),12)
        queried={key for payload in payloads for key in payload['where']['recipe_id']['$in']}
        self.assertEqual(queried,{r['recipe_id'] for r in rows})
        self.assertTrue(all(any(meal in r['meals'] for r in result) for meal in ('breakfast','lunch','dinner','snack')))
        self.assertTrue(all(payload['n_results']<=3 for payload in payloads))
