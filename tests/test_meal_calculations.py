"""Decimal arithmetic, incomplete metadata and finite planner search limits."""
from decimal import Decimal as D
import unittest
from app.services.meal_planner import (exclusion_reasons, generate, portion_entries, restrictions,
                                       totals, exact_match)


def snapshot(**overrides):
    return dict(name='Example',recipe_id=1,servings=3,meals=['breakfast','lunch','dinner','snack'],
                allergens=[],allergen_complete=True,dietary_tags=['vegetarian'],
                ingredients=[dict(food=dict(name='Test food',kcal_100g='123.456789',protein_100g='10',
                    carbs_100g='20',fat_100g='3',provenance={}),grams='100',allergens=[],
                    allergen_complete=True,ingredient_labels=['oats'])],**overrides)


class MealCalculationTests(unittest.TestCase):
    def test_round_grams_once_then_keep_nutrition_unrounded(self):
        entries=portion_entries(snapshot(),D('1'))
        self.assertEqual(entries[0]['grams'],D(100)/3)
        self.assertEqual(totals(entries)['kcal'],D('123.456789')*(D(100)/3)/100)
        self.assertEqual(totals(entries*3)['kcal'],totals(entries)['kcal']*3)

    def test_unknown_is_not_zero_and_extreme_gram_bounds(self):
        recipe=snapshot();recipe['ingredients'][0]['food']['protein_100g']=None
        self.assertIsNone(totals(portion_entries(recipe,D(1)))['protein'])
        recipe['ingredients'][0]['grams']='0.01';recipe['servings']=20
        with self.assertRaises(ValueError):portion_entries(recipe,D('.25'))
        recipe['ingredients'][0]['grams']='10000';recipe['servings']=1
        with self.assertRaises(ValueError):portion_entries(recipe,D(2))
        for value in ['0','.24','2.01','1.1','NaN','Infinity']:
            with self.subTest(value=value),self.assertRaises(ValueError):portion_entries(snapshot(),D(value))

    def test_restrictions_remain_separate_and_unknown_text_blocks(self):
        rules,errors=restrictions(dict(allergies='Peanuts, Dairy',unwanted_ingredients='Olives',dietary_preferences='Vegetarian'))
        self.assertEqual(rules,dict(allergies=['milk','peanut'],unwanted=['olives'],diets=['vegetarian']))
        self.assertEqual(errors,[])
        _,errors=restrictions(dict(allergies='coconut allergy',unwanted_ingredients='avoid olives',dietary_preferences='low carb'))
        self.assertEqual(len(errors),3)
        rules,errors=restrictions(dict(allergies='shellfish,nuts',unwanted_ingredients='',dietary_preferences=''))
        self.assertEqual(rules['allergies'],['crustaceans','molluscs','peanut','tree nuts'])
        self.assertEqual(errors,[])

    def test_declared_conflicts_unknown_review_dislikes_and_diets(self):
        recipe=snapshot()
        rules=dict(allergies=['peanut'],unwanted=[],diets=['vegetarian'])
        self.assertEqual(exclusion_reasons(recipe,rules),[])
        recipe['ingredients'][0]['allergen_complete']=False
        self.assertIn('Incomplete',exclusion_reasons(recipe,rules)[0])
        recipe['ingredients'][0]['allergen_complete']=True
        recipe['ingredients'][0]['allergens']=['peanut']
        self.assertTrue(any('Declared allergens' in reason for reason in exclusion_reasons(recipe,rules)))
        rules=dict(allergies=[],unwanted=['oats'],diets=['vegan'])
        reasons=exclusion_reasons(recipe,rules)
        self.assertTrue(any('Unwanted ingredients' in reason for reason in reasons))
        self.assertTrue(any('dietary' in reason for reason in reasons))

    def test_infeasible_targets_show_bounded_search_and_no_false_success(self):
        targets={key:'0' for key in ['kcal','protein','carbs','fat']}
        choices,notes=generate([snapshot()],targets)
        self.assertEqual(len(choices),4)
        self.assertFalse(any(choices))
        self.assertTrue(any('No nonzero' in note for note in notes))
        self.assertTrue(any('Global optimality is not guaranteed' in note for note in notes))
        self.assertFalse(exact_match(totals(portion_entries(snapshot(),D('.25'))),targets))

    def test_no_eligible_meal_is_explained_even_with_partial_targets(self):
        targets={key:'100' for key in ['kcal','protein','carbs','fat']}
        recipe=snapshot();recipe['meals']=['breakfast']
        choices,notes=generate([recipe],targets)
        self.assertIsNone(choices[1]);self.assertTrue(any('Lunch' in note for note in notes))
        choices,notes=generate([recipe],{**targets,'protein':None})
        self.assertIsNone(choices[1]);self.assertTrue(any('Lunch' in note for note in notes))

    def test_unset_targets_use_default_portions_and_partial_targets_stay_optional(self):
        targets={key:None for key in ['kcal','protein','carbs','fat']}
        choices,notes=generate([snapshot()],targets)
        self.assertEqual(len(choices),4)
        self.assertTrue(all(choice[1]==D(1) for choice in choices if choice))
        self.assertIsNone(choices[3])
        self.assertIn('No daily targets',notes[-1])
        self.assertFalse(exact_match(totals(portion_entries(snapshot(),D(1))),targets))
        choices,notes=generate([snapshot()],{**targets,'protein':'20'})
        self.assertEqual(len(choices),4)
        self.assertIsNone(targets['kcal'])


if __name__=='__main__':unittest.main()
