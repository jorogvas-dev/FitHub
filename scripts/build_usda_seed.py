"""Rebuild the bundled subset from the official SR Legacy April 2018 CSV ZIP.

Offline: python scripts/build_usda_seed.py PATH_TO_DOWNLOADED_ZIP
The checked-in manifest pins FDC IDs and exact source descriptions. No API is used.
"""
import csv
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'db/data'
URL = 'https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip'
NUTRIENTS = {'1008': ('kcal_100g', 'KCAL'), '1003': ('protein_100g', 'G'),
             '1005': ('carbs_100g', 'G'), '1004': ('fat_100g', 'G')}


def build(archive):
    manifest = json.loads((DATA / 'usda-selection.json').read_text(encoding='utf-8'))
    selected = {str(row['fdc_id']): row['description'] for row in manifest}
    with zipfile.ZipFile(archive) as bundle:
        def rows(filename):
            path = next(n for n in bundle.namelist() if n.endswith('/'+filename))
            return csv.DictReader(io.TextIOWrapper(bundle.open(path), encoding='utf-8-sig'))
        units = {r['id']: r['unit_name'].upper() for r in rows('nutrient.csv')}
        assert all(units[key] == unit for key, (_, unit) in NUTRIENTS.items())
        foods = {}
        for row in rows('food.csv'):
            source_id = row['fdc_id']
            if source_id not in selected:
                continue
            assert row['description'] == selected[source_id], 'Source description changed'
            name = row['description']
            lower = name.lower()
            preparation = ('raw' if ', raw' in lower else 'uncooked' if 'uncooked' in lower or ', dry,' in lower
                           else 'cooked' if any(x in lower for x in ('cooked', 'roasted', 'boiled', 'baked', 'toasted'))
                           else 'as described')
            foods[source_id] = dict(source_id=source_id, name=name, preparation=preparation,
                source_url='https://fdc.nal.usda.gov/download-datasets/',
                publication_date=row['publication_date'],
                **{field: None for field, _ in NUTRIENTS.values()})
        for row in rows('food_nutrient.csv'):
            if row['fdc_id'] not in foods or row['nutrient_id'] not in NUTRIENTS:
                continue
            field, _ = NUTRIENTS[row['nutrient_id']]
            value = row['amount'].strip()
            if value:
                number = Decimal(value)
                assert number.is_finite() and 0 <= number <= (1000 if field == 'kcal_100g' else 100)
                assert number == number.quantize(Decimal('.000001')), 'More than six decimal places'
                assert foods[row['fdc_id']][field] is None, 'Duplicate nutrient'
                foods[row['fdc_id']][field] = value  # Keep source precision; no missing-to-zero conversion.
        assert set(foods) == set(selected)
    payload = dict(source='USDA FoodData Central', dataset='SR Legacy, April 2018 (final)',
        download_url=URL, archive_sha256=hashlib.sha256(Path(archive).read_bytes()).hexdigest(),
        basis='100 g edible portion of the food as described; weigh the stated preparation, not refuse.',
        nutrient_ids={key: field for key, (field, _) in NUTRIENTS.items()},
        foods=sorted(foods.values(), key=lambda r: int(r['source_id'])))
    (DATA / 'usda-sr-2018.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f"Built {len(foods)} foods; SHA256 {payload['archive_sha256']}")


if __name__ == '__main__':
    build(sys.argv[1])
