# Coral / burgundy palette

Applied to the running Flask/Jinja app on `ui/purple-theme` using the shared theme
tokens, so dashboards, nutrition, training, profiles, forms and public pages inherit
the same palette. The browser theme-color is now charcoal too.

| Supplied color | Use |
| --- | --- |
| `#F7374F` | Coral accents, button borders, gradient stop and hover glow |
| `#88304E` | Burgundy buttons, selected navigation and gradient stop |
| `#522546` | Plum surfaces and gradient stop |
| `#2C2C2C` | Charcoal page background and neutral gradient stop |

Cards use a blend of the supplied plum and charcoal. Light neutral text and coral
tints keep labels readable. The primary gradient has a 22% charcoal overlay so its
brightest part retains readable white text. Secondary actions use a burgundy/plum/
charcoal gradient; destructive actions retain a distinct coral border and inset rail.

Hover shifts the gradient, adds a coral glow and lifts the button 2px. Pressing it
returns it to its resting height. Disabled buttons do not lift or glow. Keyboard
focus retains a 3px light outline. Reduced-motion preferences disable the animation
and lift. Quiet text actions stay quiet.

## Verification

**24 browser checks passed**, using read-only access to the existing synthetic demo:

- Dashboard, Nutrition diary, Exercise progress and completed workout rendered at
  **1280px and 375px**, with charcoal backgrounds and no horizontal overflow.
- Verified primary gradient, changed hover position/glow, lift, visible keyboard
  focus, reduced-motion handling and the existing destructive confirmation style.
- Sampled primary gradient label contrast was at least **4.88:1**; secondary
  gradient labels **7.78:1**; body text **13.34:1**; muted card text **8.30:1**;
  selected-navigation text **4.85:1**.
- Both rebuilt web replicas are healthy. CSS/template diagnostics and diff checks
  passed. No data, calculation, schema or dependency changes were made. The full
  PostgreSQL regression suite was not rerun for this visual-only change.
- Reviewed the desktop dashboard, hover capture and mobile nutrition screenshots.

| View | Desktop | Mobile |
| --- | --- | --- |
| Dashboard | [1280px](screenshots/coral-burgundy/dashboard-1280.png) | [375px](screenshots/coral-burgundy/dashboard-375.png) |
| Nutrition | [1280px](screenshots/coral-burgundy/nutrition-1280.png) | [375px](screenshots/coral-burgundy/nutrition-375.png) |
| Progress | [1280px](screenshots/coral-burgundy/progress-1280.png) | [375px](screenshots/coral-burgundy/progress-375.png) |
| Completed workout | [1280px](screenshots/coral-burgundy/workout-1280.png) | [375px](screenshots/coral-burgundy/workout-375.png) |

[Hover capture](screenshots/coral-burgundy/dashboard-hover-1280.png).

The Superdesign CLI was unavailable (`npx` absent from this session's PATH), so the
existing canvas previews were not updated. The running FitHub app was updated directly.

## PowerShell rebuild

```powershell
Set-Location E:\FitHub-School
docker compose build web
docker compose up -d --no-deps --wait --wait-timeout 180 web
```
