# SolarMax Web UI Functionality Inventory

| Metadata | Value |
|---|---|
| Status | Planning baseline |
| Repository | `solarmax` |
| Audited revision | `b15a4aded21fb1ddd92f9adaa015874e50267302` (`master`) |
| Audit date | 2026-09-05 |
| Evidence level | Repository-wide source read plus runtime contract probe |

## 1. Scope and method

This inventory is the compatibility baseline for the Web UI redesign. It was derived from the implementation, not from the file tree alone.

The audit covered all 29 files tracked by Git at the audited revision:

- repository metadata and runtime packaging: `.gitignore`, `README.md`, `requirements.txt`, `Dockerfile`, and `docker-compose.yml`;
- all Python modules under `solarmax/`, including FastAPI routes, Pydantic models, service logic, SQLite schema/migrations, billing, weather, MCP, and inverter adapters;
- all six Jinja templates and both static frontend files;
- all three test modules.

The repository was initially clean (`git status --short` produced no entries). The Codebase Memory full index was current for the audited commit. It reported partial template parsing at `base.html:20`, `plans.html:14-15`, and `settings.html:10,16-17,29`; those files and ranges were read directly. Deliberately ignored runtime data, caches, screenshots, and `_probe_*.py` files are not tracked and are not part of this inventory. No live database values or secrets were inspected or reproduced.

Runtime verification used FastAPI's `TestClient` with an isolated temporary SQLite database. All five HTML routes returned `200 text/html`; the JSON endpoints returned the shapes recorded below. `python -m pytest -q` passed all 24 tests. The standalone `pytest -q` launcher failed collection because its environment did not place the repository on `sys.path`; that is a runner/environment issue, not a failing application test.

## 2. System boundary

SolarMax is one Python process with four user-facing boundaries:

1. FastAPI serves five server-rendered Jinja pages.
2. FastAPI exposes JSON reads plus form-encoded mutations under `/api`.
3. A background thread polls enabled inverters through pluggable adapters and persists telemetry to SQLite.
4. A stdio MCP server calls the same `SolarmaxService` for agent access.

There is no frontend build step today. The Docker image is Python-only, copies `solarmax/` and `README.md`, installs six Python dependency families, runs as UID 10001, persists `/data/solarmax.db`, and exposes port 9117. FastAPI mounts `solarmax/static/` at `/static` and resolves templates from `solarmax/templates/`.

The browser frontend is intentionally thin:

- Jinja supplies all initial page data.
- `app.js` renders one embedded-data bar chart and toggles one disclosure.
- The browser does not poll `/api/state`, `/api/chart`, or `/api/bill`.
- Navigation and successful form submissions perform full-page loads.

## 3. Information architecture and pages

The shared top navigation links to Dashboard, Inverters, Plans & TOU, Billing, and Settings. There is no authentication, authorization, user/account model, site switcher, breadcrumb, route-level error UI, or not-found page in this repository.

### 3.1 Dashboard — `GET /`

Server inputs:

- `service.dashboard_state()`;
- `service.chart_points()` with its default 14-day limit;
- `state.bill`, also returned within dashboard state.

Visible content and controls:

| Area | Data/action | Current states and behavior |
|---|---|---|
| Site header | Site name, poll interval, mode, theme | Text only; no freshness timestamp is supplied. |
| Poll now | `POST /api/poll-now` | Full-page form submission; redirects to `/`. Polling is synchronous for the request. |
| Reachability banner | `state.all_reachable` | Shown when false; explains that live values are unavailable. |
| Live power metrics | Solar, load, grid import/export, battery charge/discharge in kW | Six numeric cards only when `state.live` exists; otherwise all show an em dash. |
| Today's energy metrics | The same six channels in kWh | Values come from local-day counters only when every enabled inverter is reachable; otherwise all show an em dash. |
| Bill chart | `chart_points`, day plus `amount_cents` | Client-rendered positive/negative vertical bars. Empty state asks the user to poll. Tooltips are native `title` attributes. |
| Current bill | `bill.total_cents` | Dollar-formatted button toggles the dashboard bill table; the toggle is not state-persistent. |
| Bill details table | `bill.rows` | Day, period, direction, kWh, nullable rate, and nullable amount. Hidden by default. |
| Context summary | Active plan, connected inverter count, mode | “Connected” is the length of all profiles, not the number enabled or reachable. |
| Inverter strip | Every profile | Name, model, IP/subnet or “not set”, enabled/reachable status, reserve, feed-in limit, export cap. |

Important semantics:

- “Live” values aggregate all enabled, reachable inverters.
- If there are no enabled inverters, if an enabled inverter has no reading, or if any enabled inverter is marked unreachable, `all_reachable` is false and both `live` and `totals` are `null`.
- Stale telemetry is never displayed as live. The UI must preserve this all-or-nothing safety rule.
- Today's solar/load/battery totals prefer device-local daily registers. Today's grid import/export uses persisted deltas from plant lifetime counters.
- The chart is not the full current bill. It groups completed telemetry rollup `amount_cents` by local day and adds the daily supply charge only to days that have a rollup. It excludes open-bucket live lines and meter-reconciliation adjustments.

### 3.2 Inverters — `GET /inverters`

Server inputs: dashboard state and `service.list_inverters()`.

For every existing inverter the page renders one editable form:

- hidden `inverter_id`;
- status chip: reachable, unreachable, or disabled;
- name;
- model;
- adapter module/kind;
- IP address;
- subnet;
- battery feed-in limit in kW;
- battery reserve percentage;
- export limit in kW;
- allow-grid-charge checkbox;
- enabled checkbox;
- notes;
- Save inverter action to `POST /api/inverters`.

The endpoint can insert when `inverter_id` is absent, but the current template does not render a blank add form. The “Edit / add inverter” heading therefore overstates the exposed UI capability.

A side panel documents the initial SigenStor adapter and contains an AI action:

- numeric inverter ID, default `1`;
- “Fetch weather and apply recommendation” submits to `POST /api/ai/apply`;
- the action redirects back to `/inverters`;
- there is no confirmation or recommendation preview in the current UI.

The separate `POST /api/ai/recommend` JSON operation is not connected to a current browser control.

Saving an inverter changes the stored profile only. `InverterAdapter.apply_profile()` is an unused merge helper, and no Modbus write functions exist. The battery and export controls are therefore policy/profile values, not verified hardware writes.

### 3.3 Plans & TOU — `GET /plans`

Server inputs:

- all power plans;
- current `active_plan_id`;
- all TOU periods for each plan, transformed from minute integers to `HH:MM` strings and keyed by plan ID for the template.

For every existing plan the page renders an editable plan form:

- hidden `plan_id`;
- provider name;
- plan name;
- billing cycle: `monthly` or `quarterly` (displayed as “3 Monthly”);
- billing start day, 1–31;
- billing start month, 1–12;
- notes;
- Save plan action to `POST /api/plans`.

Although the endpoint can insert when `plan_id` is absent, the current template does not render a blank add form. Saving any plan—new or existing—also makes it the active plan.

For every plan the page renders a second TOU form:

- daily supply charge as a currency string, default display `$0.00`;
- daily export tier threshold in kWh;
- tier-one export rate in cents/kWh;
- excess export rate in cents/kWh;
- a raw JSON textarea containing import and export periods;
- Save TOU periods action to `POST /api/tou/{plan_id}`.

Each JSON period exposes `id`, `plan_id`, `direction`, `label`, `start_minute`, `end_minute`, and `rate_cents_per_kwh`, although IDs and ownership are discarded on save. Times are presented as `HH:MM`; `24:00` is accepted only as an end-style parsed value. The backend requires start/end values to be 30-minute aligned and requires end greater than start. It does not validate overlap, gaps, duplicate labels, full-day coverage, direction-wide consistency, or a unique rate at every instant.

Saving TOU is replacement, not patching: existing periods for that plan are deleted and the submitted validated set is inserted in one database transaction. The same request also updates supply charge and all three export-tier fields.

### 3.4 Billing — `GET /billing`

Server input: `service.current_bill_summary()`.

Visible content:

- total current bill in dollars;
- today's plant-meter grid import and export in kWh;
- daily supply charge in dollars multiplied by the number of represented days;
- table with day, period, direction, kWh, rate, and amount.

Row states include:

- import charge rows;
- export credit rows with negative amounts;
- export rows split into tier-one and excess quantities;
- `Current (live)` rows for the open half-hour when a baseline and current lifetime reading exist;
- meter reconciliation at a known full-day flat rate;
- meter reconciliation with unknown TOU allocation, whose rate and amount are `null` and which may include `unpriced: true`;
- fixed daily supply-charge rows with direction `fixed` and zero kWh.

Billing uses the configured site timezone for local days and TOU matching. The plant daily counter is authoritative for total grid kWh. When interval allocation is incomplete, reconciliation preserves the real quantity and deliberately does not invent a TOU rate. The UI must not silently treat `null` amount as zero.

Billing-window fields exist on a plan, and `current_billing_window()` exists, but the current bill query does not filter telemetry to that window. Consequently the UI must not claim that the total is restricted to the configured current billing cycle without a backend correction.

### 3.5 Settings — `GET /settings`

Server input: dashboard state.

Editable fields and controls:

- theme: `classic-light`, `classic-dark`, `deep-ocean`, or `ember-core`;
- mode: `manual` or `ai`;
- site name;
- latitude;
- longitude;
- IANA timezone string;
- polling interval in seconds, with HTML min 5 and max 3600;
- active plan, including None;
- Save settings action to `POST /api/settings`.

Invalid or retired stored themes normalize to `classic-dark` when loaded. Invalid timezones normalize to `Australia/Brisbane`. Endpoint-level form parsing enforces primitive types but not the Pydantic bounds shown by the browser; direct API clients can store some values that fail later model validation. The redesign should not depend only on HTML constraints.

## 4. HTTP contract

### 4.1 Page and asset routes

| Method | Path | Response | Notes |
|---|---|---|---|
| GET | `/` | `200 text/html` | Dashboard template. |
| GET | `/inverters` | `200 text/html` | Inverter template. |
| GET | `/plans` | `200 text/html` | Plans and TOU template. |
| GET | `/billing` | `200 text/html` | Billing template. |
| GET | `/settings` | `200 text/html` | Settings template. |
| ANY | `/static/*` | Static file response | Starlette `StaticFiles`; currently CSS and JavaScript. |

### 4.2 JSON reads and actions

FastAPI currently emits an empty response schema for these operations because route response models are not declared.

#### `GET /api/state`

Request: no parameters.

Response keys:

```text
settings, inverters, power_plans, live, totals,
all_reachable, bill, theme
```

- `settings`: `AppSettings` fields serialized as JSON.
- `inverters`: database rows, including integer `enabled`, `reachable`, and `allow_grid_charge` flags.
- `power_plans`: database rows.
- `live`: six aggregated kW numbers or `null`.
- `totals`: six aggregated local-day kWh numbers or `null`.
- `all_reachable`: boolean.
- `bill`: the bill shape described below.
- `theme`: duplicates `settings.theme`.

The payload does not expose a reading timestamp, browser-safe “last updated” time, per-inverter telemetry, battery state of charge, EMS mode, forecast, or TOU periods.

#### `GET /api/chart?days=14`

`days` is an integer query parameter with default 14. No explicit lower or upper bound exists. Response:

```json
{
  "points": [
    { "day": "YYYY-MM-DD", "amount_cents": 0.0 }
  ]
}
```

Points are sorted, then sliced to the last `days` items. Negative values represent a net credit.

#### `GET /api/bill`

With a valid active plan, response keys are:

```text
plan, total_cents, rows, daily, today_grid_import_kwh,
today_grid_export_kwh, supply_charge_cents, supply_charge_days
```

`rows` is the display/audit sequence. `daily` is a separately calculated energy breakdown and excludes the fixed supply-charge rows. Values are rounded at service-defined precision.

With no valid active plan, the current response is the incompatible sparse shape:

```json
{ "total_cents": 0.0, "lines": [], "rollups": [] }
```

Clients must handle both shapes until the backend normalizes the contract additively.

#### `POST /api/close-day`

Request: empty. Response:

```json
{
  "day": "YYYY-MM-DD",
  "solar_kwh": 0.0,
  "grid_export_kwh": 0.0,
  "grid_import_kwh": 0.0
}
```

The route is not exposed by the current browser UI. It completes eligible rollups, reprices them, and returns the current local day's authoritative totals; it does not mark an immutable closed-day record.

#### `POST /api/ai/recommend`

Request: empty. Successful response:

```text
weather: source, description, min_temp_c, max_temp_c,
         rain_mm, cloud_cover_pct
recommendation: recommended_reserve_percent,
                recommended_feed_in_limit_kw,
                explanation, weather_source
```

This performs a synchronous external Open-Meteo request with a ten-second timeout. Transport and upstream errors are not translated into a stable API error shape.

### 4.3 Form-encoded mutations

All request bodies use `application/x-www-form-urlencoded`. Successful operations return `303 See Other`, except that ordinary browser/fetch clients may automatically follow the redirect and observe the final `200 text/html`. FastAPI primitive parsing and service/Pydantic validation failures normally produce `422`; only the TOU handler explicitly converts its known parsing/validation exceptions into `{ "detail": "..." }`.

#### `POST /api/settings`

| Field | Wire type | Required/default |
|---|---|---|
| `theme` | string | required |
| `mode` | string | required |
| `site_name` | string | required |
| `site_lat` | number | required |
| `site_lon` | number | required |
| `site_timezone` | string | `Australia/Brisbane` |
| `poll_interval_seconds` | integer | required |
| `active_plan_id` | string | empty means `null` |

Success redirect: `/settings`.

#### `POST /api/inverters`

| Field | Wire type | Required/default |
|---|---|---|
| `inverter_id` | integer or omitted | omitted inserts |
| `name` | string | required |
| `model` | string | required |
| `adapter_kind` | string | required |
| `ip_address` | string | empty |
| `subnet` | string | empty |
| `enabled` | boolean form value | false when absent |
| `battery_feed_in_limit_kw` | number | 0, range 0–100 in model |
| `battery_reserve_percent` | integer | 20, range 0–100 in model |
| `export_limit_kw` | number | 0, range 0–100 in model |
| `allow_grid_charge` | boolean form value | false when absent |
| `notes` | string | empty |

Success redirect: `/inverters`. An unknown non-null ID yields an update affecting no rows but still redirects successfully.

#### `POST /api/plans`

| Field | Wire type | Required/default |
|---|---|---|
| `plan_id` | integer or omitted | omitted inserts |
| `provider_name` | string | required |
| `plan_name` | string | required |
| `billing_cycle` | string | required; model allows monthly/quarterly |
| `billing_start_day` | integer | required; model range 1–31 |
| `billing_start_month` | integer | required; model range 1–12 |
| `daily_supply_charge` | currency string | `$0.00` |
| `export_tier_kwh` | number | 0 |
| `export_tier_rate_cents_per_kwh` | number | 0 |
| `export_excess_rate_cents_per_kwh` | number | 0 |
| `notes` | string | empty |

The currency parser accepts optional whitespace/`$`, digits, and up to two decimal places, then converts dollars to cents. Success always sets the saved plan active and redirects to `/plans`. As with inverters, an unknown non-null ID can silently update no record and still be selected as active.

#### `POST /api/tou/{plan_id}`

| Field | Wire type | Required/default |
|---|---|---|
| path `plan_id` | integer | required; missing plan gives 404 |
| `payload` | JSON array encoded as one form string | required |
| `daily_supply_charge` | currency string | `$0.00` |
| `export_tier_kwh` | number | 0 |
| `export_tier_rate_cents_per_kwh` | number | 0 |
| `export_excess_rate_cents_per_kwh` | number | 0 |

Browser-normalized raw line endings in the JSON string are deliberately accepted. Each period's `id` and `plan_id` are removed; `start_minute` and `end_minute` accept `HH:MM` strings or legacy integer minutes. Success redirects to `/plans`.

#### `POST /api/poll-now`

Request: empty. Polls all enabled inverters, updates reachability, writes successful readings and counters, builds completed rollups, reprices them, then redirects to `/`.

#### `POST /api/ai/apply`

Request: required integer `inverter_id`. The server independently fetches weather and computes a fresh recommendation, then updates reserve percentage and feed-in limit for that ID. The client cannot supply or pin the recommendation being applied. An unknown ID is a silent no-op. Success redirects to `/inverters`.

## 5. Polling and update behavior

### 5.1 Server polling

- FastAPI lifespan starts one daemon thread per process.
- The loop polls immediately, then waits for the persisted `poll_interval_seconds` value.
- Default interval is 30 seconds; model bounds are 5–3600 seconds.
- Any unhandled loop error is swallowed and the next wait falls back to 30 seconds.
- Each enabled inverter is polled sequentially.
- No IP or any Modbus error returns no reading, sets `reachable = 0`, and writes no telemetry.
- A successful read sets `reachable = 1`, persists raw instantaneous and cumulative values, updates local-day counters, and may complete/reprice half-hour rollups.
- Multi-worker Uvicorn deployment would start a polling thread in every worker. The current container command uses the default single worker; this is an operational compatibility constraint.

### 5.2 Browser updates

There are no browser live updates today. A page reflects a server snapshot taken at navigation time. Users see newer background-polled data only after navigation, a full reload, or Poll now. The redesign may add read-only browser refreshes, but it must distinguish “UI checked the API” from “inverter reading captured” because the current API provides no aggregate capture timestamp.

## 6. Data entities and provenance

### 6.1 `app_settings`

Key/value strings for theme, mode, polling interval, site identity/location/timezone, active plan ID, and migration markers. The typed `AppSettings` view supplies defaults and normalization.

### 6.2 `inverter_profiles`

Editable identity/network/policy fields plus enabled and reachable flags. One SigenStor profile is seeded. Unknown adapter kinds fall back to the SigenStor adapter rather than erroring.

### 6.3 `power_plans`

Provider/name, nominal billing-cycle anchor, daily fixed charge, daily export-tier threshold and two rates, and notes. One starter plan is seeded and activated.

### 6.4 `tou_periods`

Plan-owned, half-open local-wall-clock intervals with import/export direction, label, minute boundaries, and rate. Seeded import periods are Origin-style shoulder/off-peak/peak periods; the seeded export period spans the day.

### 6.5 `telemetry_raw`

Per-inverter samples containing six instantaneous kW channels, six cumulative kWh counters, six deltas, timestamp, and a `lifetime` provenance flag. Only successful real hardware reads are stored.

### 6.6 `daily_counters`

Per-inverter, per-local-day totals for the same six energy channels, plus the last observed baseline and source (`daily`, `lifetime`, `session`, or legacy) for each. These rows are the dashboard's today-total authority and the grid-meter reconciliation authority.

### 6.7 `telemetry_rollups`

Per-inverter half-hour energy buckets and precomputed net `amount_cents`. Only lifetime-sourced raw deltas may become billable rollups. The active plan can reprice historical rollups globally.

### 6.8 External/device data

- Modbus TCP input-register reads are dependency-free and read-only. No write function codes exist.
- SigenStor provides instantaneous power, lifetime counters, and some local-day counters. SOC and EMS mode are read only for logs and are not persisted or supplied to the UI.
- Open-Meteo supplies one-day temperature, precipitation, and cloud-cover inputs. There is no API key.

## 7. UI state matrix

| State | Current representation | Required compatibility meaning |
|---|---|---|
| Initial server load | Fully rendered HTML | No skeleton/loading state today. |
| No enabled or never-read inverter | Banner; all live/today metrics `—` | Do not infer zero. |
| Any enabled inverter unreachable | Banner; all aggregate live/today metrics `—` | Do not show stale partial aggregate. |
| All enabled inverters reachable | Numeric aggregate metrics | Sum enabled reachable profiles only. |
| Disabled inverter | Dimmed status chip | Excluded from aggregate live/today data and meter totals. |
| No chart points | Explanatory empty message | Do not fabricate history. |
| No active/valid plan | Bill total can display zero; API shape is sparse | Needs explicit no-plan state in React. |
| Empty bill rows | Empty table body | Needs a clear empty state. |
| Unpriced reconciliation | Em dash rate and amount | Quantity is real; monetary value is unknown, not zero. |
| Export credit | Negative amount/bar | Preserve sign and label as credit. |
| Form validation error | Browser constraints or FastAPI JSON error page | Current full-page forms do not render inline server errors. |
| Mutation success | 303 then page reload | React fetch must account for followed HTML. |
| Weather failure | Unhandled server error | Needs stable failure presentation before a polished flow. |
| Theme change | Visible after redirect/reload | Four active themes; three retired values normalize to dark. |

## 8. MCP compatibility surface

The MCP server is not a browser concern, but the Web UI migration must not break its shared service contract. It exposes:

- `get_dashboard_state()`;
- `list_inverters()`;
- `update_inverter(inverter)`;
- `list_power_plans()`;
- `set_app_settings(settings)`;
- `get_weather_and_recommendation()`;
- `apply_recommendation(inverter_id, recommendation)`.

MCP can pass a recommendation object directly to the service, while HTTP `/api/ai/apply` cannot. MCP has no TOU, billing, chart, poll-now, or close-day tool. Frontend route changes must leave the service and stdio entrypoint usable.

## 9. Backend compatibility constraints for the redesign

1. Preserve all 17 registered route entries and their current paths while migration is in progress: five page routes, eleven explicit API routes, and the static mount.
2. Preserve form field names, form encoding, `303` redirect behavior for non-JavaScript/legacy clients, and current JSON keys. Additive response fields and endpoints are safer than replacements.
3. Do not display stale, partial, simulated, or derived-as-measured energy. `null` live/totals means unavailable, not zero.
4. Do not expose SOC, per-inverter live power, capture freshness, tariff allocation, self-consumption, savings, carbon, forecasts, or historical solar/load charts unless the backend begins supplying those values with defined provenance.
5. Keep timezone conversion and half-hour TOU matching on the backend. The browser may format dates but must not recalculate authoritative billing.
6. Treat money as cents at the API boundary and only format dollars for display/input. Preserve negative export credits and nullable unpriced amounts.
7. The current API does not provide TOU periods as JSON. A complete React replacement of `/plans` requires an additive typed read endpoint or a stable expansion of `/api/state`; scraping the legacy HTML is not acceptable.
8. FastAPI response models are absent. Generated TypeScript types would currently be incomplete until response schemas are declared or a hand-maintained, tested contract layer is introduced.
9. Form mutation endpoints can silently succeed for unknown inverter/plan IDs, and settings validation is weaker at write time than at read time. The frontend should surface these as known backend hardening tasks, not conceal them with optimistic success.
10. The active billing-cycle fields are not applied to the bill query. Do not label the current total as billing-window-scoped until that behavior is defined and tested.
11. Keep the polling process single-owner. Frontend polling reads state; it must not repeatedly trigger hardware polling.
12. Preserve read-only Modbus behavior. Profile edits and AI application are persisted policy changes, not evidence of device configuration.
13. Preserve the Python-only runtime until a reviewed deployment phase adds a deterministic frontend build artifact. Production cannot depend on a Node server.
14. Avoid reading or bundling SQLite/runtime data, environment values, network addresses, notes, or other installation-specific values into the static build.

## 10. Existing verification coverage

The 24 passing tests cover:

- local-time TOU boundaries and date grouping;
- export tier splitting;
- Origin import-period migration and metadata preservation;
- lifetime/session source transitions and midnight baselines;
- plant-meter reconciliation, including unknown TOU pricing;
- close-day totals and stale telemetry cleanup;
- currency parsing and daily supply charge behavior;
- enabled/disabled/reachable aggregate dashboard semantics;
- direct daily-register versus lifetime-grid provenance;
- SigenStor register selection and decoding;
- retired and supported theme loading;
- browser-normalized control characters in TOU JSON form submission.

Not covered today:

- page-route or static-asset browser behavior as a suite;
- accessibility, responsive layout, themes by screenshot, or JavaScript chart behavior;
- most API request/response contracts and redirect/error cases;
- browser polling/concurrency;
- weather failure/success behavior;
- Docker image build or container health;
- MCP regression;
- multi-inverter per-device telemetry presentation, because that data is not exposed.

## 11. Confirmed gaps and redesign blockers

The redesign is feasible, but a complete SPA cannot ship honestly without resolving these contract gaps:

- no JSON read contract for TOU periods;
- untyped JSON response schemas in OpenAPI;
- inconsistent no-plan bill shape;
- no aggregate telemetry capture timestamp;
- mutation responses designed only for page redirects;
- no stable weather-error response;
- no authentication or CSRF protection, significant if the service is reachable beyond a trusted LAN;
- billing-cycle configuration is not applied to bill selection;
- current chart totals do not equal the complete displayed bill in all cases;
- some endpoints silently accept nonexistent record IDs;
- the current UI says “connected inverters” when it counts all profiles.

These are implementation inputs and acceptance risks, not reasons to invent client-side data. The companion design document sequences the required additive backend contract work before replacing each page.
