# SolarMax React WebUI parity and release record

## Route-and-feature parity matrix

| Legacy view / route | React route | Displayed data | Forms and actions | API calls | Loading / empty / error | Responsive status | Implementation |
|---|---|---|---|---|---|---|---|
| Dashboard `/` | `/` | Site name, mode, poll cadence, observed state, telemetry availability, live energy cards, chart series | Poll inverters; refresh shell data | `GET /api/state`, `GET /api/chart`, `POST /api/poll-now` | Skeleton; unavailable telemetry warning; API error with retry | Desktop 1440, tablet 1024 labelled rail, mobile 390 drawer; no horizontal overflow | Complete |
| Inverter profiles `/inverters` | `/inverters` | Every persisted inverter, reachability/status, model, adapter, network, limits, reserve and notes | Add/edit profile; enabled/grid-charge toggles; save; preview recommendation; re-check and apply | `GET /api/state`, `POST /api/inverters`, `POST /api/ai/recommend`, `POST /api/ai/apply` | Skeleton; no-profile empty state; per-form mutation errors and success notices | Responsive two-column-to-one-column form; touch-sized controls; no overflow | Complete |
| Power plans `/plans` | `/plans` | Provider, plan name, billing cycle/start, supply charge, export tiers, notes; TOU periods including `24:00` day-end values | Add/edit plan; select plan; add/remove TOU period; edit direction/label/time/rate; replace/save schedule | `GET /api/state`, `GET /api/plans/{id}/tou`, `POST /api/plans`, `POST /api/tou/{id}` | Skeleton; no-plan empty state; plan-keyed TOU loading; TOU load/save errors and notices | Responsive forms; bounded TOU table scroll; mobile width verified | Complete |
| Billing `/billing` | `/billing` | Backend-authoritative total, import/export energy, supply charge, bill plan and audit rows | Refresh; confirmed close-day/finalize action; filter audit by all/import/export/fixed | `GET /api/bill`, `POST /api/close-day` | Skeleton; no active plan warning; empty audit state; retry and mutation errors | Summary cards stack on mobile; audit table contained; no overflow | Complete |
| Settings `/settings` | `/settings` | Theme, operating mode, site identity, coordinates, IANA timezone, polling cadence, active plan | Edit and save settings; theme applies immediately and persists via API | `GET /api/state`, `POST /api/settings` | Skeleton; API error state; save success/error notice | Responsive form at all target viewports; full-value plan title affordance | Complete |

## API and workflow parity

- `GET /api/state` is the shared source for dashboard, inverter, plan and settings pages.
- `GET /api/chart` remains available for historical/live chart consumers; the overview renders the returned series through the shared chart component.
- `GET /api/bill` remains backend-calculated and authoritative; the React audit filter is presentation-only.
- `POST /api/close-day` remains available to existing integrations and is covered by Python contract tests.
- Plan, TOU, settings and inverter mutations submit to their existing backend endpoints; no mock production data or client-side billing calculations were introduced.
- Recommendation preview and apply use separate existing endpoints, with an explicit re-check/apply action.

## Visual QA artifacts

Production-browser screenshots for every route at every acceptance viewport are stored in:

`/home/sarah/.hermes/cache/browser-use/workspace/20260906_091624_df4ea4ac/screenshots/`

Files are named `{desktop|tablet|mobile}-{overview|inverters|plans|billing|settings}.png`; contact sheets for each viewport are also present. The final browser pass found no horizontal overflow at 1440x900, 1024x768, or 390x844. Desktop, tablet and mobile contact sheets were visually inspected after the final responsive repair.

## Deliberate differences from the legacy interface

- The legacy server-rendered pages are retained as fallback/compatibility routes, while React is the selected production presentation when `SOLARMAX_WEBUI_MODE=react`.
- The React UI uses a shared dark operational design system, responsive navigation, clearer loading/error states, accessible names and visible focus rings.
- The billing page keeps server-authoritative calculations and intentionally does not reimplement a cycle-window total in the browser.
- Long identifiers retain their exact values and expose the full value through native title affordances rather than widening every mobile field.

## Verification record

- Python: `python -m pytest -q` — 43 passed.
- Frontend typecheck: `npm run typecheck` — passed.
- Frontend lint: `npm run lint` — passed with zero warnings.
- Frontend unit tests: `npm test -- --run` — passed.
- API drift: `npm run api:check` — passed.
- Production build: `npm run build` — passed.
- Production e2e: `npm run e2e` — 8 passed; the script builds first on a clean checkout.
- Visual browser QA: 15 route/viewport captures; no overflow, console-visible errors, or broken route navigation observed.
- Accessibility: Playwright axe checks passed on compact navigation; labelled controls and visible focus styles verified.
- Docker build/runtime checks are recorded in `docs/WEBUI_ROLLOUT_RECOVERY_REVIEW.md`.
- `git diff --check` — passed.
- Standalone Hadolint is optional and unavailable locally; Docker build-stage lint checks remain enabled and pass.

## Docker run

```sh
docker build --progress=plain -t solarmax-webui:local .
docker run --rm -p 9117:9117 -v solarmax-data:/data solarmax-webui:local
```

The runtime image defaults to React mode, runs as the unprivileged `solarmax` user, serves exactly the current built React bundle, and preserves `/data/solarmax.db` as the authoritative persistent database. Set `SOLARMAX_WEBUI_MODE=legacy` only for an explicit compatibility rollback.

## Rollback

Do not push or deploy the recovery branch automatically. For a local rollback, stop the container and restore the prior image tag or prior LXC/PBS snapshot. Preserve `/data/solarmax.db`; application state and secrets remain in the deployment’s persistent data volume/root filesystem according to the existing operational procedure.

## Known limitations

- Live inverter telemetry is unavailable when no enabled inverter is reachable; the UI deliberately shows an explicit unavailable/stale state instead of fabricated values.
- Hadolint cannot be executed independently until the optional executable/container is available; this does not block the project’s configured Docker checks.
- The visual QA browser session used the seeded test database and local backend; hardware reachability and weather-provider results require the deployment environment.
