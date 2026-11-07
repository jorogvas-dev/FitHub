# Recipe data and license

Recipe data: **UniTools — theunitools.com**, [dataset page](https://theunitools.com/en/data),
licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
The full upstream [LICENSE](LICENSE) accompanies the unmodified dataset
[unitools-recipes-v1.json](unitools-recipes-v1.json).

Source: https://github.com/farcrak/unitools-recipes

Pinned commit: `2f554073f7e730b18d707fe1164d18308f6ec16c`.

Dataset SHA256: `41fdc0572b7272ca557d2df22a0fac1cc1f18146e97ebd2c8258c395daf87d8a`.

[manifest.json](manifest.json) records both data and license checksums and the
explicit 100-recipe measured collection. The previous pilot selection and public
PostgreSQL snapshots are retained in [pilot-v1-selection.json](pilot-v1-selection.json)
and [pilot-v1-snapshots.json](pilot-v1-snapshots.json) for reproducible comparison.
Higher protein is a curation tag for at least 20 g per source serving; meal prep
means at least four source batch servings. These are selection labels, not claims
that every meal fits every target.

FitHub adaptations: selection of 100 recipes; English text extraction; meal,
search and dietary tags; separately reviewed declared ingredient matching labels;
conversion of numerical quantities to decimal strings for precise storage.
Upstream per-serving macros, original batch quantities and step instructions are
retained. Images are not imported; their separate image licenses are not used.
FitHub's adapted recipe data and ingredient review metadata are also CC BY-SA 4.0.
The license applies to these data files; no application-code license is changed.

Allergen labels support ingredient matching, not cross-contact certification.
Compound ingredients, unspecified oils and unreviewed variants remain incomplete.
An active allergy excludes any recipe containing an incomplete ingredient.

The [validation report](validation-report.json) lists every rejected source recipe
and reason, plus valid recipes outside the priority selection. Missing quantities
are never replaced with estimates. Thirty-two pilot recipes with unspecified
quantities are retired from retrieval; their PostgreSQL rows and saved snapshots
are retained. Breads, desserts, drinks and sauces are classified as snacks rather
than complete main meals. Low-fat protein means at least 20 g protein and at most
10 g fat per recorded serving; quick means recorded prep + cook time at most
30 minutes. These labels do not infer unrecorded preparation or chilling time.
New identity mappings with `reviewed: false` do not assert allergen completeness.
All adapted data, including baseline snapshots and validation metadata, remain
under the accompanying CC BY-SA 4.0 terms.
