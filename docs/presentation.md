# Six-minute demonstration

Use a fictional account you register yourself, for example `school_demo`, with a
password you keep locally. Do not show real account data or a credential file.
Submission screenshot accounts are temporary and are removed after verification.

## Prepare fictional history before class

Save a fictional profile (175 cm, 75 kg, general fitness, beginner) and training
preferences (three days, 45 minutes, available equipment). Make a plan named
“School demo — full body”, with Bench press, 3 × 6–12 and 90 seconds of rest.
Complete one session at 40 kg × 8, then another at 45 kg × 9. These establish a
baseline, a strict PR and two chart points even on the same day. Add two fictional
weigh-ins on different dates within the current week. Schedule a plan day for today
and another for tomorrow. None of these values are advice for a real person.

## Demo timeline (about six minutes)

| Time | Show and explain |
|---|---|
| 0:00–0:40 | Log in. Dashboard combines weekly workouts, new records, nutrition and upcoming workouts. Explain private accounts and fictional data. |
| 0:40–1:10 | Show onboarding/profile and optional editable training preferences. Existing bodyweight/profile records are preserved. |
| 1:10–2:00 | Click Generate workout draft. During the local model wait, explain Open WebUI → LM Studio and server validation. Review the draft, estimated duration and explicit Save as new plan. If the provider fails, show its error and continue with the manual plan. |
| 2:00–2:50 | Start the prepared training day, add a fictional set, show prescribed rest, pause/+15/reset and reload persistence. Finish explicitly. |
| 2:50–3:35 | Show calendar, date rescheduling and completed session. Open Progress: table/chart, load record and separate bodyweight rep records. Open the prepared completed session's New PR badges. |
| 3:35–4:50 | Set sample caps 2000 kcal / 140 g protein / 230 g carbs / 65 g fat. Generate nutrition with FitHub Coach. Explain exact upper limits, portions, optional snack and remaining shortfalls. Name the plan; explicitly save. Show Swap this meal and preview/confirm; diary logging is separate. |
| 4:50–5:25 | Show an exclusion or allergy and explain why unknown ingredient declarations block recipes. Restore demo preferences explicitly. Show normal user denial of admin; use a separately promoted fictional admin only if needed. |
| 5:25–6:00 | Show the architecture diagram, real-vs-mocked test results, verified backup restore and isolated fresh-install result. Finish with limitations: bounded search, incomplete allergens, optional AI and unsupported timed logging. |

If model waits exceed the allotted time, use the prepared manual plans and the
recorded live-check evidence. Do not repeatedly submit or claim a fallback is AI.

## Presentation-day startup checklist

1. Start Docker Desktop. Start the existing LM Studio model server and Open WebUI
   using their normal controls. Do not change presets/models immediately before class.
2. Run the commands below. Keep credentials in their existing ignored files.
3. Confirm two healthy web replicas and healthy PostgreSQL/Chroma/nginx/Dozzle.
4. Open HTTPS, accept the local certificate, log in to the fictional demonstration
   account and confirm the saved example plans/history. Open a narrow mobile view too.
5. Run model discovery and one synthetic nutrition smoke. If AI is unavailable,
   prepare the manual path and state the limitation honestly.
6. Confirm the external verified backup exists. Avoid migrations/builds or failover
   experiments during the presentation itself.

```powershell
Set-Location E:\FitHub-School
docker compose up -d --wait --wait-timeout 300
docker compose run --rm --no-deps -T web flask --app app db-upgrade
docker compose exec -T web flask --app app import-recipes --index
docker compose ps
docker compose exec -T nginx nginx -t
docker compose exec -T web flask --app app nutrition-models
docker compose exec -T web flask --app app nutrition-smoke --kcal 2000 --protein 140 --carbs 230 --fat 65
Start-Process https://localhost:8443/dashboard
```

If deploying a newly checked-out version beforehand, first run:

```powershell
docker compose build web nginx
docker compose run --rm --no-deps -T web flask --app app db-upgrade
docker compose up -d --wait --wait-timeout 300
```

Do not reset volumes or display the environment, key file, database backup or raw
container environment variables on the projector.
