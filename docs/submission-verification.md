# School submission verification — 7 November 2026

Branch: `ui/purple-theme`. Origin was verified as
`https://github.com/jorogvas-dev/FitHub.git`. The submission commits the implemented
work accumulated since the previous checkpoint, including migrations 007–013 and
the current UI. Existing unrelated local tooling remains outside the release.

## Actual results

| Check | Result | What was real or mocked |
|---|---|---|
| Docker/PostgreSQL suite | **290 passed**, zero skipped, **101.053 s** | Real PostgreSQL; isolated schemas; model responses/failures mocked in regression cases |
| Current deployment HTTP smoke | **22 passed** | Real HTTPS/login/onboarding/plan/log/calendar/progress/PR/recap/nutrition/admin requests; temporary fictional account; no inference calls |
| Fresh installation | **22 HTTP checks passed** | Separate project/ports/volumes; new generated secrets, no AI credentials; migrations + 100-recipe import/index; cleanup confirmed |
| Browser workout generation | **22 checks passed**, live draft **33.417 s** | Existing Open WebUI/LM Studio model; three training days, duplicate-ID checks, private draft and explicit-save UI |
| Browser nutrition target fitting | **18 checks passed**, live generation **15.870 s** | Real fithub-coach preset, PostgreSQL, Chroma and exact Decimal fitting; stale targets rejected |
| Browser Workout Review | **24 checks passed**, live review **6.103 s**, two suggestions | Real provider returned AI mode; separately rendered mocked-provider-failure recap also checked |
| Browser meal swaps | **52 checks passed**, live explanation **6.804 s**, three explanations | Real recipe data/ranking/confirmation; optional Coach request; historical diary unchanged |
| Browser personal records | **39 checks passed** | Temporary fictional weighted/bodyweight history, deterministic record derivation |
| Browser weekly recap | **32 checks passed** | Temporary fictional sessions/measurements and saved timezone |
| Browser calendar/rest timer | **44 checks passed** | Real forms, reloads and tab switching; delayed-tick passage and blocked audio simulated with browser APIs |
| External backup restoration | **Passed**, all **23 public-table row counts matched** | Real custom-format dump restored to a separate database, then temporary DB removed |
| Deployment | Healthy db, Chroma, nginx, Dozzle and two identical web replicas | Existing secrets/volumes preserved; nginx configuration valid |
| Open WebUI credentials | Authenticated `/api/models` returned **200 from both replicas** | Exact preset `fithub-coach` present; keys never printed |

The seven current browser groups total **231 checks**, each including relevant
**1280 px / 375 px** layouts. They use normal HTTPS forms and labelled synthetic
data. All screenshot accounts and their rows were cleaned up. The pre-existing
demonstration/user accounts were not reset. No JavaScript page errors were found
in the rest/calendar checks; no horizontal page overflow was found in the reported
responsive checks. Physical-speaker audibility is not claimed.

The HTTP smoke also proves an empty milk-allergy-safe catalogue fails closed before
inference and creates no draft. It checks regular-member admin denial and temporary
explicit admin promotion, then removes that promoted test account. The regression
suite additionally covers unknown ingredients, invalid model IDs/numbers, provider
failure/timeout, account isolation, concurrent starts, preserved snapshots and
exact/fractional caps on swaps, edits and saving.

## Live nutrition evidence

The live CLI smoke separately completed in **12.262 s**, using synthetic caps
2000 kcal / 140 g protein / 230 g carbs / 65 g fat. Both it and the browser fit
returned **2000 kcal / 122.5 g protein / 227.5 g carbs / 64.75 g fat**.
Remaining amounts: **0 kcal / 17.5 g / 2.5 g / 0.25 g**. An optional eligible snack
was fitted. No upper limit was exceeded; targets stayed unchanged. The CLI smoke
made zero database writes. Browser generation created only a draft until confirmed.

Live endpoints reuse Open WebUI at
`http://host.docker.internal:3000/api/chat/completions`; nutrition uses
`fithub-coach`, while workout/review use the existing
`qwen2.5-7b-nerd-uncensored-v1.0`. No model, preset, knowledge collection or tool in
the other AI repository was changed. Earlier feature reports that recorded an
unreachable provider are historical; current submission live checks succeeded.

## Backup and isolated resources

Verified backup and exact restoration commands are in
[backup-and-restore.md](backup-and-restore.md). Backups stay outside Git and contain
private data; no dump or credential is committed. The main backup's table counts
matched after restoring to a uniquely named isolated database.

Fresh verification ran successfully as `fithub-fresh-8b57cd2f`, then again with the
final 22-check smoke as `fithub-fresh-2ccb57b7`. Containers, networks, volumes,
temporary image tags and generated key/config directories were removed. Main
deployment volumes and the external backup remain. The final verifier refuses
pre-existing resources for its selected project name before creating anything.

A final fresh installation from an exported **staged Git checkout**, project
`fithub-fresh-ba7513e8`, also passed all 22 checks and was cleaned up. This exported
tree contained no ignored environment/key files. Pinned dataset/license hashes
matched after Windows checkout. Release preparation fixed a reproducibility risk
by marking those upstream files byte-preserving in .gitattributes; otherwise Git
line-ending conversion could invalidate their checksums.

## Release review and scope

The release includes application source/templates/assets, Compose/nginx, additive
schema changes, tests, public licensed reference data, documentation and synthetic
screenshots. Full diff/content review checks ownership, validation/confirmation
paths, migrations, source provenance, deployment and data boundaries. A credential
scan compares source against the actual local secret values without printing them.
Private-key/API-key patterns, tracked data paths and staged whitespace are checked.

Files deliberately outside the release:

- Untracked `.claude/` design-tool customization, preserved unchanged.
- Ignored `.env`, `.secrets/`, local virtual environment, scratch scripts/logs/exports
  in `tmp/`, caches, private documents and model weights.
- PostgreSQL/Chroma/TLS volumes and external PostgreSQL backups.
- The separate AI project's repository, presets, tools, knowledge, containers and volumes.

There are no outstanding service/credential verification blockers for this
submission. The localhost/security, timed-logging, allergen-coverage, source-estimate
and bounded-search limitations in [the school guide](school-project.md) remain.
The tag is created only after final checks and the branch push succeed; an existing
v1.0.0 tag is never moved.

## Current temporary-account screenshots

| Feature | Desktop | Mobile |
|---|---|---|
| Dashboard / weekly recap | [1280](screenshots/release/weekly-recap/this-week-1280.png) | [375](screenshots/release/weekly-recap/this-week-375.png) |
| Live workout draft | [1280](screenshots/release/workout-ai/generated-duplicate-fix-live-1280.png) | [375](screenshots/release/workout-ai/generated-duplicate-fix-live-375.png) |
| Rest timer / active workout | [1280](screenshots/release/calendar/active-timer-1280.png) | [375](screenshots/release/calendar/active-timer-375.png) |
| Workout calendar | [1280](screenshots/release/calendar/calendar-1280.png) | [375](screenshots/release/calendar/calendar-375.png) |
| Live nutrition fit | [1280](screenshots/release/nutrition/fitted-preview-1280.png) | [375](screenshots/release/nutrition/fitted-preview-375.png) |
| Meal swap alternatives | [1280](screenshots/release/meal-swaps/swap-protein-1280.png) | [375](screenshots/release/meal-swaps/swap-protein-375.png) |
| Live Workout Review | [1280](screenshots/release/workout-review/review-live-1280.png) | [375](screenshots/release/workout-review/review-live-375.png) |

See the [presentation checklist](presentation.md) for exact startup commands.
