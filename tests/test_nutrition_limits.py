"""Exact hard caps, normalized fit, partial feasibility and optional snacks."""
from decimal import Decimal as D
import unittest
from app.services.meal_planner import generate, totals, portion_entries
from app.services import nutrition_limits as limits
from app.services.nutrition import amount


def recipe(key='main',slots=None,**macros):
    values=dict(kcal='100',protein='5',carbs='10',fat='1');values.update(macros)
    return dict(recipe_id=key,name=key,catalogue_recipe=True,servings=1,meals=slots or ['breakfast','lunch','dinner'],macros=values,
        dietary_tags=[],allergens=[],allergen_complete=True,ingredients=[dict(food=dict(name='Rice',source_allergens=[]),allergens=[],allergen_complete=True,ingredient_labels=['rice'])])


def value(choices):
    return totals([entry for choice in choices if choice for entry in portion_entries(*choice)])


class NutritionLimitTests(unittest.TestCase):
    def test_exact_limits_are_inclusive_and_display_rounding_never_permits_an_overage(self):
        values=dict(kcal=D('100'),protein=D('0'),carbs=None,fat=None)
        self.assertEqual(limits.errors(values,dict(kcal='100',protein='0')),[])
        values['kcal']=D('100.004')
        self.assertEqual(amount(values['kcal']),'100')
        self.assertTrue(limits.errors(values,dict(kcal='100')))
        choices,_=generate([recipe(slots=['breakfast'],kcal='100.004')],dict(kcal='100'))
        self.assertEqual(value(choices)['kcal'],D('75.003'))

    def test_normalization_makes_calories_and_grams_comparable_including_fractional_limits(self):
        values=dict(kcal=D(250),protein=D('.25'),carbs=D(0),fat=D(0))
        self.assertEqual(limits.shortfall(values,dict(kcal='500',protein='.5')),D('.5'))

    def test_snack_is_added_only_for_a_strict_improvement_under_every_cap(self):
        rows=[recipe(),recipe('snack',['snack'])]
        targets=dict(kcal='800',protein='40',carbs='80',fat='8')
        choices,notes=generate(rows,targets)
        self.assertIsNotNone(choices[3])
        self.assertEqual(value(choices),{key:D(number) for key,number in targets.items()})
        self.assertTrue(any('optional eligible snack' in note for note in notes))
        choices,_=generate(rows,dict(kcal='600',protein='30',carbs='60',fat='6'))
        self.assertIsNone(choices[3])

    def test_snack_that_crosses_any_limit_is_never_added(self):
        rows=[recipe(),recipe('snack',['snack'],fat='100')]
        targets=dict(kcal='800',fat='6')
        choices,_=generate(rows,targets)
        self.assertIsNone(choices[3])
        self.assertEqual(limits.errors(value(choices),targets),[])

    def test_missing_targets_do_not_impose_limits_or_add_a_purposeless_snack(self):
        rows=[recipe(kcal='5000'),recipe('snack',['snack'])]
        choices,_=generate(rows,{})
        self.assertEqual([choice[1] for choice in choices if choice],[D(1)]*3)
        self.assertIsNone(choices[3])
        self.assertEqual(value(choices)['kcal'],D(15000))

    def test_conflicting_targets_return_a_feasible_partial_result_without_changing_targets(self):
        targets=dict(kcal='300',protein='100',fat='2')
        original=targets.copy()
        choices,notes=generate([recipe()],targets)
        self.assertEqual(limits.errors(value(choices),targets),[])
        self.assertEqual(targets,original)
        self.assertTrue(any('shortfall' in note for note in notes))
        self.assertLess(value(choices)['protein'],D(100))

    def test_unknown_unlimited_nutrients_stay_unknown_and_capped_unknowns_are_rejected(self):
        rows=[recipe(slots=['breakfast'],protein=None)]
        choices,_=generate(rows,dict(kcal='100'))
        self.assertIsNone(value(choices)['protein'])
        choices,notes=generate(rows,dict(kcal='100',protein='50'))
        self.assertFalse(any(choices))
        self.assertTrue(any('No nonzero' in note for note in notes))
        self.assertEqual(limits.errors(value(choices),dict(protein='50')),[])

    def test_zero_targets_are_real_caps_and_missing_meal_slots_are_explained(self):
        choices,notes=generate([recipe(slots=['breakfast'])],dict(kcal='0'))
        self.assertFalse(any(choices))
        self.assertEqual(value(choices)['kcal'],D(0))
        self.assertTrue(any('Partial day' in note for note in notes))

    def test_best_single_slot_fit_matches_exhaustive_portion_search(self):
        rows=[recipe('a',['breakfast'],kcal='101',protein='20'),recipe('b',['breakfast'],kcal='140',protein='25')]
        targets=dict(kcal='185',protein='28')
        choices,_=generate(rows,targets)
        scores=[limits.shortfall(dict(kcal=D(0),protein=D(0),carbs=D(0),fat=D(0)),targets)]
        for row in rows:
            for quarter in range(1,9):
                values=totals(portion_entries(row,D(quarter)/4))
                if not limits.errors(values,targets):scores.append(limits.shortfall(values,targets))
        self.assertEqual(limits.shortfall(value(choices),targets),min(scores))
