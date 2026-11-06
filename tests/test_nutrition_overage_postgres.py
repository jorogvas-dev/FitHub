"""Overage labels distinguish a positive rounded-zero amount from exact zero."""
from datetime import datetime, timezone
from decimal import Decimal as D
from unittest.mock import patch

from flask import template_rendered

from test_plans_postgres import PostgresTestCase

DAY = '2026-06-11'


class NutritionOverageTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        clock = patch('app.services.nutrition.utc_now',
                      return_value=datetime(2026, 6, 11, 12, tzinfo=timezone.utc))
        clock.start()
        self.addCleanup(clock.stop)
        self.assertEqual(self.post(self.a, '/nutrition/settings', timezone='UTC',
                                   target_kcal=0, target_protein=0,
                                   target_carbs=0, target_fat=0).status_code, 302)

    def food(self, value):
        self.assertEqual(self.post(self.a, f'/nutrition/diary/{DAY}/entries/new',
                                   logged_on=DAY, meal='lunch', grams=100,
                                   food_name='Overage check', kcal_100g=value,
                                   protein_100g=value, carbs_100g=0, fat_100g=0).status_code, 302)

    def views(self):
        for path, key in [('/nutrition?date='+DAY, 'totals'), ('/dashboard', 'nutrition_totals')]:
            contexts = []
            def capture(sender, template, context, **extra):
                contexts.append(context)
            with template_rendered.connected_to(capture, self.app):
                response = self.a.get(path)
            self.assertEqual(response.status_code, 200)
            yield response.get_data(as_text=True), contexts[-1][key]

    def test_small_positive_overages_and_exact_zero_on_both_pages(self):
        self.food('0.49')
        for html, totals in self.views():
            for nutrient, unit in [('kcal', 'kcal'), ('protein', 'g')]:
                self.assertIn(f'Less than 1 {unit} over target', html)
                self.assertEqual(totals[nutrient]['over'], D('0.49'))
                self.assertEqual(totals[nutrient]['remaining'], D('-0.49'))
                self.assertEqual(totals[nutrient]['total'], D('0.49'))
            self.assertIn('0 g remaining', html)  # The other two nutrients are exactly zero.
            self.assertNotIn('0 kcal over target', html)
            self.assertNotIn('0 g over target', html)

    def test_tiny_incomplete_overages_are_still_known_lower_bounds(self):
        self.food('0.01')
        self.food('')
        for html, totals in self.views():
            for nutrient, unit in [('kcal', 'kcal'), ('protein', 'g')]:
                self.assertIn(f'Known subtotal: Less than 1 {unit} over target; actual overage may be higher.', html)
                self.assertEqual(totals[nutrient]['over'], D('0.01'))
                self.assertIsNone(totals[nutrient]['remaining'])
                self.assertIsNone(totals[nutrient]['total'])
                self.assertFalse(totals[nutrient]['complete'])

    def test_exact_zero_is_never_an_overage(self):
        self.food('0')
        for html, totals in self.views():
            self.assertNotIn('Less than 1', html)
            self.assertNotIn('class="over-target"', html)
            self.assertNotIn('metric-balance over-target', html)
            for nutrient, unit in [('kcal', 'kcal'), ('protein', 'g')]:
                self.assertIn(f'0 {unit} remaining', html)
                self.assertIsNone(totals[nutrient]['over'])
                self.assertEqual(totals[nutrient]['remaining'], D(0))

    def test_half_up_boundary_keeps_the_normal_integer_label(self):
        self.food('0.50')
        for html, totals in self.views():
            self.assertNotIn('Less than 1', html)
            self.assertIn('1 kcal over target', html)
            self.assertIn('1 g over target', html)
            self.assertEqual(totals['protein']['over'], D('0.50'))
