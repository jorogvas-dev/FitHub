-- USDA retired the old per-food URL route. Link to the verified download source;
-- permanent FDC IDs and the exact archive URL remain recorded independently.
-- Only our original bundled source links change; administrator custom URLs survive.
UPDATE food_catalogue
SET source_url='https://fdc.nal.usda.gov/download-datasets/'
WHERE source='USDA FoodData Central'
  AND dataset_version='SR Legacy, April 2018 (final)'
  AND source_url='https://fdc.nal.usda.gov/food-details/'||source_id||'/nutrients';
