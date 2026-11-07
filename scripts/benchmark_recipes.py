"""Read-only collection comparison; synthetic targets, no account data or writes.

Capture baseline first: pipe the public recipe_catalogue rows as JSON to a file.
Run inside the web container with that file mounted. Default provider is mocked;
--live performs separate calls through the existing FitHub nutrition provider.
"""
import argparse
from decimal import Decimal as D
import json
from pathlib import Path
import sys
from time import monotonic
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app
from app.db import connect
from app.routes.plans import query
from app.services import recipe_catalogue as catalog, nutrition_ai, nutrition_limits
from app.services.meal_planner import exclusion_reasons, portion_entries, totals
from app.services.starter_recipes import candidates as starter_candidates


def coverage(rows):
    return dict(recipes=len(rows),breakfasts=sum('breakfast' in r['meals'] for r in rows),
        low_fat_protein=sum(D(r['macros']['protein'])>=20 and D(r['macros']['fat'])<=10 for r in rows),
        vegetarian=sum('vegetarian' in r['dietary_tags'] for r in rows),
        quick=sum(r['prep_minutes']+r['cook_minutes']<=30 for r in rows),
        snacks=sum('snack' in r['meals'] for r in rows),
        meal_prep=sum(r['servings']>=4 for r in rows),
        fully_reviewed_allergens=sum(r['allergen_complete'] for r in rows))


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--baseline',required=True,type=Path)
    parser.add_argument('--live',action='store_true')
    args=parser.parse_args()
    baseline=json.loads(args.baseline.read_text(encoding='utf-8-sig'))
    with connect() as conn:
        expanded=query(conn,'SELECT * FROM recipe_catalogue WHERE is_active ORDER BY recipe_id')
        snacks,_=starter_candidates(conn,dict(allergies=[],unwanted=[],diets=[]),require_nutrition=False)
    collections={'previous':[catalog.snapshot(r) for r in baseline if r['is_active']],
                 'expanded':[catalog.snapshot(r) for r in expanded]}
    cases=[('balanced',dict(kcal='2000',protein='140',carbs='230',fat='65'),[],[]),
           ('lean',dict(kcal='1800',protein='150',carbs='180',fat='50'),[],[]),
           ('vegetarian',dict(kcal='2000',protein='120',carbs='250',fat='60'),[],['vegetarian']),
           ('milk_allergy',dict(kcal='2000',protein='140',carbs='230',fat='65'),['milk'],[]),
           ('unset',dict(kcal=None,protein=None,carbs=None,fat=None),[],[])]
    if args.live:cases=cases[:1]
    report=dict(provider='live fithub-coach' if args.live else 'mocked structured preferences; real PostgreSQL, Chroma and Decimal fitting',
                coverage={name:coverage(rows) for name,rows in collections.items()},cases=[])
    for name,rows in collections.items():
        for label,targets,allergies,diets in cases:
            rules=dict(allergies=allergies,unwanted=[],diets=diets)
            eligible=[r for r in rows if not exclusion_reasons(r,rules)]
            eligible_snacks=[r for r in snacks if not exclusion_reasons(r,rules)]
            started=monotonic()
            if not eligible:
                report['cases'].append(dict(collection=name,case=label,eligible=0,blocked='No eligible recipes; exclusions retained.',seconds=round(monotonic()-started,4)))
                continue
            shortlist=nutrition_ai.shortlist(eligible,'higher protein quick breakfast meal prep',targets)
            def run():
                return nutrition_ai.generate(shortlist,targets,'higher protein quick breakfast meal prep',with_notes=True,snacks=eligible_snacks,candidates=eligible)
            if args.live:
                try:choices,notes=run()
                except Exception:
                    report['cases'].append(dict(collection=name,case=label,eligible=len(eligible),shortlist=len(shortlist),
                        error='Live provider unavailable or response invalid; no draft or diary created.',seconds=round(monotonic()-started,4)))
                    continue
            else:
                with patch('app.services.workout_ai.nutrition',return_value=json.dumps(dict(meals={m:None for m in ('breakfast','lunch','dinner','snack')}))):
                    choices,notes=run()
            values=totals([entry for choice in choices if choice for entry in portion_entries(*choice)])
            assert not nutrition_limits.errors(values,targets)
            report['cases'].append(dict(collection=name,case=label,eligible=len(eligible),shortlist=len(shortlist),
                targets=targets,totals=values,remaining=nutrition_limits.remaining(values,targets),
                normalized_shortfall=nutrition_limits.shortfall(values,targets),
                selections=[dict(recipe_id=c[0]['recipe_id'],portion=c[1]) for c in choices if c],
                seconds=round(monotonic()-started,4),notes=notes))
    print(json.dumps(report,default=str,indent=2))


if __name__=='__main__':
    with create_app().app_context():main()
