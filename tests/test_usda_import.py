"""Offline importer distinguishes absent, blank and explicit zero nutrients."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile
from scripts import build_usda_seed


class USDAImportTests(unittest.TestCase):
    def archive(self,directory,amount='12.345678'):
        archive=directory/'fixture.zip'
        with zipfile.ZipFile(archive,'w') as bundle:
            bundle.writestr('fixture/food.csv','fdc_id,description,publication_date\n1,"Fixture, raw",2019-04-01\n')
            bundle.writestr('fixture/nutrient.csv','id,unit_name\n1008,KCAL\n1003,G\n1005,G\n1004,G\n')
            bundle.writestr('fixture/food_nutrient.csv',f'fdc_id,nutrient_id,amount\n1,1008,{amount}\n1,1003,0\n1,1004,\n')
        (directory/'usda-selection.json').write_text(json.dumps([dict(fdc_id=1,description='Fixture, raw')]))
        return archive

    def test_missing_is_null_zero_is_zero_and_rebuild_is_byte_repeatable(self):
        with TemporaryDirectory() as temporary:
            directory=Path(temporary);archive=self.archive(directory)
            with patch.object(build_usda_seed,'DATA',directory):
                build_usda_seed.build(archive)
                before=(directory/'usda-sr-2018.json').read_bytes()
                build_usda_seed.build(archive)
                self.assertEqual((directory/'usda-sr-2018.json').read_bytes(),before)
            food=json.loads(before)['foods'][0]
            self.assertEqual(food['kcal_100g'],'12.345678')
            self.assertEqual(food['protein_100g'],'0')
            self.assertIsNone(food['carbs_100g']);self.assertIsNone(food['fat_100g'])
            self.assertEqual(food['preparation'],'raw')

    def test_nonfinite_and_out_of_range_source_data_are_rejected(self):
        for value in ['NaN','Infinity','-1','1001']:
            with self.subTest(value=value),TemporaryDirectory() as temporary:
                directory=Path(temporary);archive=self.archive(directory,value)
                with patch.object(build_usda_seed,'DATA',directory),self.assertRaises(AssertionError):
                    build_usda_seed.build(archive)
