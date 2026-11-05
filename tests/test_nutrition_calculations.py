from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from app.services.nutrition import amount, daily_totals, diary_date, entry_totals, target_comparison, user_today

D = Decimal


def entry(grams='100', kcal='200', protein='10', carbs='25', fat='6'):
    return dict(grams=D(grams), **{key+'_100g': D(value) if value is not None else None
                for key,value in [('kcal',kcal),('protein',protein),('carbs',carbs),('fat',fat)]})


class NutritionCalculationTests(unittest.TestCase):
    def test_exact_portion_and_only_final_rounding(self):
        row=entry(grams='33.33',kcal='0.01',protein='0.01',carbs='0.01',fat='0.01')
        self.assertEqual(entry_totals(row)['kcal'],D('0.003333'))
        total=daily_totals([row,row,row],{})['kcal']
        self.assertEqual(total['total'],D('0.009999'))
        self.assertEqual(amount(total['total']),'0.01')
        self.assertEqual(amount(entry_totals(row)['kcal']),'<0.01')
        self.assertEqual(entry_totals(entry(grams='125.55',kcal='123.45'))['kcal'],D('154.991475'))

    def test_unknown_values_and_explicit_zero_are_distinct(self):
        rows=[entry(kcal=None,protein='0',carbs=None),entry(kcal='100',protein='0',carbs=None)]
        totals=daily_totals(rows,{'target_kcal':D('200')})
        self.assertFalse(totals['kcal']['complete'])
        self.assertEqual(totals['kcal']['subtotal'],D('100'))
        self.assertIsNone(totals['kcal']['total'])
        self.assertIsNone(totals['kcal']['remaining'])
        self.assertEqual(totals['kcal']['missing'],1)
        self.assertTrue(totals['protein']['complete'])
        self.assertEqual(totals['protein']['total'],D('0'))
        self.assertEqual(totals['carbs']['known_count'],0)
        self.assertIsNone(totals['carbs']['total'])
        self.assertEqual(amount(None),'Unknown')
        self.assertEqual(amount(D('0')),'0.00')

    def test_over_target_exact_remaining_and_partial_lower_bound(self):
        settings={'target_kcal':D('150'),'target_protein':D('10')}
        totals=daily_totals([entry(kcal='200')],settings)
        self.assertEqual(totals['kcal']['over'],D('50'))
        self.assertEqual(totals['kcal']['remaining'],D('-50'))
        self.assertEqual(totals['protein']['remaining'],D('0'))
        partial=daily_totals([entry(kcal='200'),entry(kcal=None)],settings)['kcal']
        self.assertEqual(partial['over'],D('50'))
        self.assertIsNone(partial['remaining'])
        self.assertFalse(partial['complete'])
        tiny=daily_totals([entry(grams='0.01',kcal='0.01')],{'target_kcal':D('0')})['kcal']
        self.assertGreater(tiny['over'],0)
        self.assertEqual(amount(tiny['over']),'<0.01')

    def test_empty_day_and_unset_targets(self):
        totals=daily_totals([],{'target_kcal':D('2000')})
        self.assertEqual(totals['kcal']['total'],D('0'))
        self.assertTrue(totals['kcal']['complete'])
        self.assertEqual(totals['kcal']['remaining'],D('2000'))
        self.assertIsNone(totals['fat']['target'])
        self.assertIsNone(totals['fat']['remaining'])

    def test_target_comparison_is_explanatory_and_never_mutates(self):
        settings=dict(target_kcal=D('2000'),target_protein=D('100'),target_carbs=D('200'),target_fat=D('50'))
        original=settings.copy()
        comparison=target_comparison(settings)
        self.assertEqual(comparison['estimated'],D('1650'))
        self.assertEqual(comparison['difference'],D('350'))
        self.assertEqual(comparison['direction'],'higher')
        self.assertEqual(settings,original)
        self.assertIsNone(target_comparison({**settings,'target_fat':None}))
        self.assertEqual(target_comparison({**settings,'target_kcal':D('1600')})['direction'],'lower')

    def test_user_today_uses_iana_rules_across_midnight_year_and_dst(self):
        instant=datetime(2026,1,1,22,30,tzinfo=timezone.utc)
        self.assertEqual(user_today('Europe/Sofia',instant),date(2026,1,2))
        self.assertEqual(user_today('America/Los_Angeles',instant),date(2026,1,1))
        self.assertEqual(user_today('Pacific/Kiritimati',datetime(2025,12,31,12,tzinfo=timezone.utc)),date(2026,1,1))
        # Summer +03 versus winter +02 changes the date at the same UTC hour.
        self.assertEqual(user_today('Europe/Sofia',datetime(2026,7,1,21,30,tzinfo=timezone.utc)),date(2026,7,2))
        self.assertEqual(user_today('Europe/Sofia',datetime(2026,1,1,21,30,tzinfo=timezone.utc)),date(2026,1,1))
        with self.assertRaises(ValueError):
            user_today('Europe/Sofia',datetime(2026,1,1))

    def test_date_validation_and_bounds(self):
        self.assertEqual(diary_date('2024-02-29'),date(2024,2,29))
        for value in ['2023-02-29','20261105','2026-W45-4','2101-01-01','1899-12-31','',None]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                diary_date(value)
