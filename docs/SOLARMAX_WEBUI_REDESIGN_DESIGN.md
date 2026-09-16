# SolarMax Web UI Redesign Design

| Metadata | Value |
|---|---|
| Status | Proposed implementation plan |
| Companion baseline | `docs/SOLARMAX_WEBUI_FUNCTIONALITY_INVENTORY.md` |
| Target | Complete five-page migration, not a dashboard reskin |

## 1. Decision summary

Migrate the complete browser UI to a statically built React 19 application using TypeScript, Vite, Tailwind CSS v4, selected shadcn/ui components backed by Radix primitives, Lucide icons, and React Router. Keep FastAPI, `SolarmaxService`, SQLite, background polling, billing calculations, Modbus behavior, and MCP as the system of record. Serve the production build from FastAPI; do not run Node in production.

Use Recharts for the existing daily net-bill visualization only, subject to a measured bundle and accessibility gate. It is technically justified here because the chart must correctly handle positive charges and negative credits, responsive axes/tooltips, and a data-table equivalent. It must not become a pretext for unsupported solar, battery, savings, or forecast charts.

The migration must be additive and reversible:

- retain every existing URL and API request shape;
- declare and normalize response contracts before generating TypeScript types;
- add the missing TOU read contract and conservative telemetry freshness metadata;
- preserve legacy form redirects for ordinary browser clients;
- run the React application and Jinja implementation behind a server-side rollout switch until parity gates pass;
- keep all energy and money calculations on the backend.

This design takes hierarchy from Linear, restraint from Vercel, dense operational legibility from Sentry, and explicit action semantics from Raycast. These are principles, not visual references to clone. The target should feel like a quiet local control surface: compact, factual, and operational. It must avoid glass effects, giant hero copy, decorative gradients, excessive cards, chatbot framing, “AI sparkle” motifs, and generic marketing-dashboard layouts.

### 1.1 Architecture review corrections (Phase 0)

The following corrections are contract hardening work, not new UI data or a React implementation:

1. Recommendation preview is inverter-specific. `POST /api/ai/recommend` accepts the selected inverter ID and computes its proposal from that profile's current reserve and feed-in values. Unknown supplied IDs return `404`; apply uses the same selected ID. The legacy no-ID request remains temporarily compatible until the React client is deployed.
2. Saving a TOU schedule and its daily-supply/export-tier tariff fields is one database transaction. Validation occurs before replacement, and any error leaves both the previously stored periods and tariff fields unchanged.
3. Mutation content negotiation is explicit. Legacy form submissions retain their existing `303` redirect targets. When `Accept: application/json` is sent, successful form mutations return `MutationSuccessResponse` (`ok`, `redirect_to`, optional `resource_id`, optional `data`), and malformed or validation failures return `MutationErrorResponse`. `POST /api/ai/recommend` retains its legacy weather/recommendation body for non-negotiated clients and returns the success envelope only for JSON-negotiated callers; its OpenAPI response documents both shapes.

### 1.2 Billing-window decision (Phase 0)

Billing-cycle start fields determine the current bill window. Monthly plans run from the configured billing day in the previous/current month; quarterly plans use the configured anchor month and day. The normalized bill contract exposes `billing_window_applied: true` when an active plan is present.

## 2. Goals and non-goals

### Goals

1. Replace Dashboard, Inverters, Plans & TOU, Billing, and Settings as one coherent application shell.
2. Preserve all existing browser, API, data-provenance, polling, billing, and MCP capabilities.
3. Make unavailable, stale, unpriced, empty, pending, failed, disabled, and reachable states unmistakable.
4. Make plan and inverter editing safer without moving business rules into the browser.
5. Establish a generated TypeScript boundary from explicit FastAPI response/request models.
6. Improve navigation, action clarity, responsive use, keyboard access, and dense-table readability.
7. Produce a deterministic static artifact that the existing FastAPI container can serve.
8. Enable staged cutover and immediate rollback without database reversal.

### Non-goals

- No backend rewrite, separate frontend server, GraphQL layer, WebSocket service, or cloud dependency.
- No device-control claims: Modbus remains read-only and profile changes remain persisted policy values.
- No invented SOC, self-consumption, savings, carbon, weather generation forecast, per-inverter power, or telemetry freshness.
- No client-side billing, TOU pricing, timezone authority, meter reconciliation, or export-tier calculation.
- No authentication design hidden inside a visual refresh. Authentication/CSRF is a separately gated deployment concern.
- No redesign of the MCP tool surface except regression protection.
- No new data visualization library beyond Recharts, and no Recharts usage where a number, status, table, or small CSS treatment is clearer.

## 3. Product truths that the UI must preserve

These are architectural invariants, not presentation preferences:

1. `live === null` and `totals === null` mean unavailable. They must never be formatted as zero.
2. Aggregate live and today values are visible only when every enabled inverter is reachable and has a latest reading.
3. A disabled inverter is excluded from site totals even if it has old telemetry.
4. Export money is a negative credit. Meter-reconciliation money can be unknown (`null`) even when its kWh is authoritative.
5. Chart points represent completed interval net amounts plus eligible supply charges, not the complete current bill in every case.
6. Local day and TOU allocation are backend responsibilities using the configured IANA timezone.
7. Direct device-day counters and plant lifetime counters have different provenance. The UI may label the result but may not recompute it.
8. AI mode is a persisted preference, not proof of automatic control. Applying an HTTP AI recommendation recalculates it from fresh weather at application time.
9. Poll now invokes hardware polling. Periodic browser refresh must only read state.
10. Billing-cycle fields determine the active monthly or quarterly window, and the bill contract declares `billing_window_applied: true` for an active plan.
11. The MCP server shares the service layer and must continue working when page rendering changes.

## 4. Target architecture

```text
Browser
  React app shell and route modules
    generated API types -> typed fetch client -> domain adapters
      same-origin FastAPI routes
        SolarmaxService
          SQLite / billing / weather / inverter adapters

Build time only
  TypeScript + Vite + Tailwind v4 + shadcn CLI
    hashed static build -> solarmax/static/webui/

Production runtime
  Uvicorn/FastAPI only
    /static/webui/* -> hashed assets
    five page URLs -> webui/index.html (or legacy Jinja by rollout switch)
    /api/* -> preserved API handlers
    MCP stdio -> preserved SolarmaxService calls
```

### 4.1 Proposed source boundaries

```text
web/
  src/
    app/                 # router, providers, shell, route-error boundaries
    api/
      generated.ts       # generated; never hand-edited
      client.ts          # fetch, form encoding, error normalization
      adapters.ts        # wire rows -> UI domain types
    components/
      ui/                 # selected shadcn-generated primitives
      shell/              # rail, mobile nav, site status, page header
      energy/             # metric and energy-flow components
      billing/            # money, trend, reconciliation, bill table
      forms/              # field, error summary, dirty-state guard
    features/
      dashboard/
      inverters/
      plans/
      billing/
      settings/
    hooks/                # visibility-aware polling and media preferences
    lib/                  # formatting only; no business calculations
    styles/
      app.css             # Tailwind import, @theme mapping, semantic tokens
  index.html
  vite.config.ts
  tsconfig.json
  package.json
  package-lock.json

solarmax/static/webui/    # generated production output; not authored source
```

The exact artifact tracking policy should be decided with deployment ownership. Preferred production flow is a multi-stage Docker build that generates the artifact and copies it into the Python image; source-control should not carry hashed build output unless deployments cannot build it deterministically.

### 4.2 State ownership

- FastAPI/SQLite owns persisted and calculated state.
- React route loaders/hooks own server snapshots and request lifecycle state.
- Local component state owns open/closed disclosures, draft fields, table sort, and temporary filters.
- URL query parameters own shareable filters such as billing day/direction and selected plan.
- Theme is applied from `settings.theme` and may be optimistically previewed; persistence still flows through `/api/settings`.
- No global Redux-style store is required. A small API cache/provider and route-level hooks are sufficient for five routes.

## 5. Information architecture and app shell

### 5.1 Primary navigation

Keep the existing five destinations and URLs:

1. Overview — `/`
2. Inverters — `/inverters`
3. Plans & TOU — `/plans`
4. Billing — `/billing`
5. Settings — `/settings`

“Overview” is a clearer navigation label than “Dashboard”; the document title and route remain compatible. Do not add an “AI” top-level page because AI currently has one narrow recommendation workflow and no independent state model.

### 5.2 Desktop shell

- A 224 px left rail contains product name, five icon-plus-text destinations, and the current mode near the bottom.
- A compact top context bar contains site name, reachability summary, API check state, and a clearly labeled Refresh data action.
- Page content uses a maximum readable width around 1440 px but allows dense tables to use the full available width.
- Page headers contain one title, one short operational description, and at most one primary action. Secondary actions go in a labeled overflow menu.
- Navigation remains visually subordinate to live warnings and destructive/high-impact actions.

### 5.3 Tablet and mobile shell

- Below 1024 px, the rail collapses to icons with accessible labels; no content relies on hover tooltips.
- Below 768 px, replace the rail with a compact top bar and Radix Sheet navigation.
- Keep primary page actions in the page header; do not create a persistent bottom action bar that competes with device browser chrome.
- Avoid multi-column metric mosaics on narrow screens. Metric groups become two columns above 420 px and one column below it.
- Dense billing and TOU tables use a horizontally scrollable region with a visible affordance and sticky first column; critical edit workflows may switch to stacked rows.

### 5.4 Global status semantics

The top bar may show:

- “All enabled inverters reachable” only from `all_reachable === true`;
- “Telemetry unavailable” when false;
- “Checking…” while `/api/state` is in flight;
- “API refresh failed” when a read fails, while retaining the last rendered snapshot with an explicit stale UI banner;
- “Checked at HH:MM:SS” based on client receipt time;
- “Observed at …” only after the backend supplies the defined `live_observed_at` field.

Client receipt time must never be labeled as inverter capture time.

## 6. Visual design system

### 6.1 Tone

Use flat, layered surfaces and deliberate separators. The page background, navigation, and content surface should be distinguishable without blur or decorative backdrop effects. Cards are reserved for independently actionable or comparable objects; ordinary content uses sections, rows, and bordered groups.

Use sentence case throughout. Prefer direct verbs: “Poll inverters”, “Save inverter”, “Preview recommendation”, “Re-check and apply”, “Add plan”, and “Save TOU schedule”. Avoid “magic”, “smart”, or promotional descriptions.

### 6.2 Typography

- Use a system UI sans stack; do not add a font network dependency.
- Base text: 14 px/20 px on desktop, 16 px form controls on mobile to avoid input zoom.
- Page title: 24–28 px, medium/semibold; no oversized hero type.
- Section title: 16–18 px, semibold.
- Labels and table headers: 12–13 px, medium; uppercase only for very short status metadata.
- Numeric telemetry and money use tabular numerals.
- Units are visually quieter but remain in accessible text.

### 6.3 Spacing, shape, and elevation

Map Tailwind utilities to a 4 px spacing basis. Use 8 px and 12 px corner radii; reserve pills for compact status badges, not every button. Use borders for ordinary separation and one restrained shadow level for dialogs/popovers. Minimum interactive height is 40 px desktop and 44 px touch contexts.

### 6.4 Semantic token contract

Define all colors as CSS custom properties and expose them to Tailwind v4 using `@theme inline`. Components consume semantic roles, never raw palette names.

```css
:root {
  --background: ...;
  --surface: ...;
  --surface-raised: ...;
  --foreground: ...;
  --muted-foreground: ...;
  --border: ...;
  --focus-ring: ...;
  --action: ...;
  --action-foreground: ...;
  --success: ...;
  --warning: ...;
  --danger: ...;
  --energy-solar: ...;
  --energy-load: ...;
  --energy-grid-import: ...;
  --energy-grid-export: ...;
  --energy-battery-charge: ...;
  --energy-battery-discharge: ...;
  --chart-positive: ...;
  --chart-negative: ...;
}
```

Theme mapping:

| Theme | Foundation | Accent behavior |
|---|---|---|
| `classic-light` | warm-neutral light surfaces | restrained blue action color |
| `classic-dark` | neutral charcoal surfaces | clear cool-blue action color |
| `deep-ocean` | blue-charcoal surfaces, not a gradient scene | cyan-blue action and focus |
| `ember-core` | warm charcoal/brown surfaces, not a gradient scene | amber action and focus |

Energy channel colors remain semantically stable across themes, adjusted only for contrast. Every channel also has an icon, label, and direction text; color is never the sole cue. Status colors meet WCAG AA against their surface for meaningful text. Automated theme contrast checks are an acceptance gate.

## 7. Full page-by-page replacement map

### 7.1 Overview (`/`)

Replace the current hero and twelve equal cards with four operational regions:

1. **Site status strip** — site name, mode, configured poll cadence, reachability, conservative observed time when available, and Poll inverters.
2. **Live energy** — a compact energy-flow topology plus six exact live channel values. When unavailable, the entire region has a prominent reason and every measurement remains `—`.
3. **Today** — six local-day totals grouped as generation/usage, grid, and battery pairs. Include a provenance note linking to the inventory-level explanation, not a false “real-time” label.
4. **Cost and fleet** — lazy-loaded daily net-amount chart, current bill summary, unpriced-warning count, and an inverter health table.

The current expandable bill table moves to a concise bill preview with “View billing details”; the full audit table belongs on `/billing`. Preserve access to all current information without duplicating a large hidden table in the overview DOM.

Components:

- `SiteStatusStrip`
- `PollInvertersButton`
- `ReachabilityAlert`
- `EnergyFlow`
- `MetricPairGroup`
- `TodayEnergySummary`
- `DailyNetAmountChart`
- `CurrentBillSummary`
- `InverterHealthTable`

States:

- initial loading skeleton with fixed geometry;
- all reachable;
- no enabled inverters;
- enabled inverter unreachable/never read;
- empty billing history;
- no active plan;
- unpriced billing rows;
- API read failed while an older snapshot exists;
- poll request running, succeeded, partially/unreachable, or failed.

### 7.2 Inverters (`/inverters`)

Use a master/detail layout:

- left/top list of inverter profiles with name, model, enabled state, reachable state, and configured/not-configured network status;
- “Add inverter” primary action, making the already-supported insert behavior genuinely available;
- selected inverter editor in a stable panel or route-addressable sheet;
- grouped fields: Identity, Network, Battery policy, Grid/export policy, and Notes;
- explicit copy explaining that Save updates SolarMax's profile and does not prove a hardware write;
- disabled and unreachable are distinct states;
- unknown adapter kinds must be shown exactly and flagged as falling back server-side, until backend validation is tightened.

Recommendation flow:

1. Select a specific inverter; never ask the user to type a raw ID.
2. “Preview recommendation” calls `/api/ai/recommend` and shows weather source, inputs, proposed reserve/feed-in changes, and explanation.
3. “Re-check weather and apply” calls `/api/ai/apply` for that inverter. Explain that the server recalculates at apply time, so the applied values may differ from the preview.
4. On success, refetch state and announce the saved profile values.
5. On timeout/upstream failure, preserve the current profile and show a retryable error.

Components:

- `InverterList`
- `InverterStatusBadge`
- `InverterEditor`
- `NetworkFields`
- `PolicyFields`
- `RecommendationPreview`
- `SaveStateBanner`

### 7.3 Plans & TOU (`/plans`)

Use a plan list plus selected-plan workspace:

- plan rows show provider/name, active state, cycle metadata, supply charge, and export tier summary;
- “Add plan” submits the existing insert form shape;
- editing a plan clearly discloses current behavior: Save makes it active;
- plan identity/cycle fields are separated from tariff fields;
- selected TOU schedule is a structured editor with direction, label, start, end, and rate columns;
- Add period, duplicate, reorder, and remove are draft-only client actions;
- time controls use 30-minute steps and support `24:00` as the day endpoint;
- import and export periods are visibly separated;
- a read-only 24-hour band previews coverage using submitted periods only—no pricing calculations;
- warnings identify overlaps and gaps. Until backend validation changes, warnings should not silently reinterpret or auto-correct a persisted schedule;
- an Advanced JSON view preserves the current raw-edit capability and round-trips the same period fields;
- Save sends the full replacement JSON string plus supply/tier fields to `/api/tou/{plan_id}`.

This page cannot be replaced until `GET /api/plans/{plan_id}/tou` (or an equivalent stable state expansion) is available. The recommended response is canonical numeric minutes:

```json
{
  "plan_id": 1,
  "periods": [
    {
      "id": 1,
      "plan_id": 1,
      "direction": "import",
      "label": "Shoulder",
      "start_minute": 0,
      "end_minute": 540,
      "rate_cents_per_kwh": 25.3
    }
  ]
}
```

Formatting numeric minutes as `HH:MM` remains a UI concern; validation and persistence remain backend concerns.

Components:

- `PlanList`
- `PlanEditor`
- `ActivePlanBadge`
- `TariffSummary`
- `TouScheduleEditor`
- `TouPeriodRow`
- `TouCoverageBand`
- `AdvancedTouJson`
- `UnsavedChangesGuard`

### 7.4 Billing (`/billing`)

Prioritize auditability over decoration:

- summary row: current computed total, today's import/export, represented supply-charge days, and active plan;
- explicit no-plan state with a link to Plans;
- explicit banner when one or more monetary amounts are unpriced;
- filters for day, direction, and priced/unpriced state stored in the URL;
- dense table with day, period, direction, kWh, rate, amount, and state;
- fixed charge rows labeled “Fixed charge”; export rows labeled “Credit”; null monetary values labeled “Unpriced”, never `$0.00`;
- optional grouped day subtotals calculated only as presentation sums of supplied non-null `amount_cents`; do not fold unknown values into a misleading complete subtotal;
- “Close/recalculate today” can expose `/api/close-day` in a secondary action menu with confirmation text explaining that it completes eligible buckets and reports totals but does not permanently lock the day;
- the daily net chart may be linked from Overview, but Billing remains useful with charts disabled.

Do not label the amount as “current billing cycle” until the backend applies `billing_start_day`, `billing_start_month`, and cycle. Use “Computed total for available records” in the interim.

Components:

- `BillingSummary`
- `UnpricedBillingAlert`
- `BillingFilters`
- `BillBreakdownTable`
- `MoneyValue`
- `DirectionBadge`
- `CloseDayDialog`

### 7.5 Settings (`/settings`)

Group fields into Site, Data refresh, Appearance, Operating mode, and Billing defaults:

- Site: name, latitude, longitude, timezone;
- Data refresh: server polling interval and explanation that browser refresh does not poll hardware;
- Appearance: four labeled theme swatches with accessible selected state;
- Operating mode: manual/AI with factual text that AI mode alone does not perform automatic hardware control;
- Billing defaults: active plan or None;
- Save settings with inline field errors, an error summary, and dirty-state navigation guard.

Theme preview may be immediate, but Cancel/error restores the persisted theme. Timezone remains a text/combobox field only if the backend supplies a supported-zone list; otherwise preserve the free-form IANA input rather than shipping a stale client list.

Components:

- `SettingsForm`
- `SettingsSection`
- `ThemePicker`
- `ModeSelector`
- `ActivePlanSelect`
- `FormErrorSummary`

## 8. Component boundaries and interaction rules

### 8.1 shadcn/Radix boundary

Adopt only primitives used by a defined workflow:

- Button, Input, Textarea, Label, Select, Checkbox;
- Alert, Badge, Separator, Skeleton;
- Dialog for confirmation, Sheet for mobile navigation/editor, Tooltip for supplementary help only;
- Dropdown Menu for secondary actions;
- Tabs only for structured/advanced TOU modes;
- Toast or a dedicated status region for transient request completion;
- Table styling, with semantic native table markup retained.

Generated shadcn source is owned by the repository. Keep modifications localized and record upstream component names. Radix primitives provide focus management and interaction semantics; they do not remove the need for labels, descriptions, error relationships, or contrast testing.

### 8.2 Domain component rules

- `MetricValue` formats a supplied number and unit or renders unavailable; it never calculates a metric.
- `MoneyValue` accepts cents or `null`, formats locale-aware dollars, and preserves credit/unpriced semantics.
- `EnergyFlow` accepts only the six supplied instantaneous channels plus availability state.
- `ReachabilityAlert` receives an explicit reason model derived from state; it does not inspect arbitrary truthiness.
- Editors convert typed drafts to the existing form field names in one API adapter.
- Page modules do not call `fetch` directly.
- Formatting functions are pure and tested. Billing, tariff, energy, and timezone business logic does not enter `web/src/lib`.

### 8.3 Action hierarchy

- One filled primary action per page/working panel.
- Neutral secondary buttons for refresh, preview, and navigation.
- Destructive styling only for actions that delete or irreversibly replace data. TOU Save gets confirmation when periods were removed because replacement deletes existing rows before insertion within the transaction.
- Icon-only controls require accessible names and visible tooltips, but prefer text labels for uncommon actions.
- Pending actions disable duplicate submission without disabling unrelated navigation.
- Success copy states what was persisted, not what a device allegedly did.

## 9. Typed API boundary

### 9.1 Backend contract hardening first

Before React consumes production APIs, add explicit Pydantic response models without removing current keys:

- `DashboardStateResponse`;
- `LivePower` and `DailyEnergyTotals`;
- `InverterResponse` with defined boolean/int wire compatibility;
- `PowerPlanResponse`;
- `TouPeriodResponse` and `TouPeriodsResponse`;
- normalized `BillSummaryResponse`, `BillRowResponse`, and `ChartResponse`;
- `CloseDayResponse`;
- `WeatherRecommendationResponse`;
- a consistent mutation success/error envelope for JSON-negotiated responses.

Normalize the no-plan bill response so it always contains `plan`, `rows`, `daily`, today totals, supply-charge fields, and total. Retain legacy `lines` and `rollups` temporarily if external consumers may use them; mark them deprecated in OpenAPI rather than deleting them.

Add `GET /api/plans/{plan_id}/tou`. Add a conservative `live_observed_at` to `/api/state`, defined as the oldest timestamp among the latest samples included in a reachable aggregate. This prevents the aggregate from appearing fresher than its stalest contributor. It is `null` whenever live data is unavailable.

### 9.2 Preserve form compatibility, add JSON negotiation

Keep all current `application/x-www-form-urlencoded` request field names and default redirect behavior. For the React client, let the same handlers return a typed JSON result when `Accept: application/json` is present:

```json
{
  "ok": true,
  "resource_id": 1,
  "redirect_to": "/inverters"
}
```

An ordinary form post still receives the existing `303`. This avoids duplicate mutation endpoints and maintains legacy/no-script behavior. If content negotiation makes OpenAPI ambiguous, additive `/api/v2` JSON mutations are acceptable, but the original routes must remain tested and supported.

Tighten backend behavior while preserving successful valid requests:

- validate complete settings at write time;
- return 404 for unknown non-null inverter/plan IDs;
- translate known weather errors into a stable 502/504 detail shape;
- bound `days` to an agreed positive maximum;
- document or correct the billing-window behavior separately;
- do not silently coerce unknown adapter kinds in new writes.

### 9.3 Type generation and adapters

Generate `web/src/api/generated.ts` from FastAPI OpenAPI with `openapi-typescript`. Check the generated file in so frontend CI does not need a running API, and add a drift check that regenerates and fails on differences.

The handwritten client layer must:

- prefix only same-origin paths;
- set explicit `Accept` and request encoding;
- parse JSON by content type;
- normalize FastAPI's validation detail into field/global errors;
- handle legacy followed-HTML responses during rollout;
- support cancellation with `AbortController`;
- never log full payloads containing IP addresses, notes, or site coordinates;
- map integer SQLite flags to domain booleans in one adapter;
- represent the legacy sparse bill response as a temporary discriminated union until backend normalization ships.

No runtime schema library is required once FastAPI owns explicit response models and contract tests exercise real serialization. Add one only if the team decides the frontend must defend against independently versioned backends; that is not the current single-image architecture.

## 10. Data fetching, polling, and freshness

### 10.1 Initial and periodic reads

- On route entry, fetch `/api/state`; fetch `/api/chart` only where the chart is visible; fetch TOU for the selected plan only on `/plans`.
- Refresh `/api/state` at the configured `poll_interval_seconds`, clamped client-side to a reasonable UI-read floor such as 10 seconds.
- Pause periodic reads while `document.visibilityState !== "visible"` and refresh once on return.
- On network failure, use capped exponential retry independent of hardware poll cadence.
- Abort obsolete requests on route change and ignore out-of-order responses.
- Do not call `/api/poll-now` on an interval. Only an explicit user action invokes hardware polling.

### 10.2 Manual poll

1. User selects Poll inverters.
2. Button shows pending state and prevents duplicates.
3. POST `/api/poll-now` with JSON negotiation or tolerate the legacy redirect response.
4. Refetch `/api/state`, `/api/bill`, and `/api/chart` after completion.
5. Report “Telemetry remains unavailable” if one or more enabled inverters still fail; do not present the HTTP success as inverter success.

### 10.3 Cache policy

API responses should be `no-store` unless the backend explicitly defines validators. Hashed static assets should be immutable with a long cache lifetime; `index.html` should be no-cache so it cannot point at removed chunks after deployment. Do not add a service worker in the first migration because stale offline control surfaces are unsafe and operationally confusing.

## 11. Energy flow and chart strategy

### 11.1 Energy flow

Render a code-native SVG or CSS-grid topology with four labeled nodes: Solar, Home load, Battery, and Grid. It receives exactly:

- `solar_kw`;
- `load_kw`;
- `grid_import_kw` and `grid_export_kw`;
- `battery_charge_kw` and `battery_discharge_kw`.

Rules:

- Show each supplied channel as its own labeled value; do not net import/export or charge/discharge in the client.
- Directional connectors reflect the named channel only. They do not assert physical conservation across asynchronously sampled sensors.
- If a channel is zero, show `0.00 kW` and a static connector. If aggregate live is unavailable, show em dashes and no animation.
- Respect `prefers-reduced-motion`; any active-flow motion is subtle and nonessential.
- Do not show battery SOC, capacity, time-to-empty, self-consumption, or per-inverter contribution.
- Provide an adjacent semantic list with the same values so the diagram is not the only accessible representation.

### 11.2 Daily net-amount chart

Use Recharts `BarChart` for `/api/chart` points:

- x-axis: local-day string formatted for display;
- y-axis: signed currency;
- zero reference line;
- separate semantic colors for charge and credit;
- tooltip with exact day, signed dollars, and “charge”/“credit” text;
- responsive container with stable reserved height;
- empty and error states outside the SVG;
- an accessible compact data table or list containing every plotted value.

Label it “Completed interval net amount by day”, not “bill forecast” or “total bill trend”. Recharts is lazy-loaded with the overview cost region. It is rejected if the measured lazy chunk exceeds the agreed bundle budget or if it cannot meet keyboard/screen-reader acceptance; the fallback is the accessible table plus a small tested SVG, not another chart library.

No additional charts are supported by the current API. Billing rows and today's totals should remain tables/numbers until time-series endpoints with explicit provenance exist.

## 12. Responsive and accessibility design

Target WCAG 2.2 AA for all routes and four themes.

### 12.1 Required behavior

- A skip link moves focus to the main content.
- Every route has one `h1`; headings are hierarchical.
- Current navigation uses `aria-current="page"`.
- All controls have persistent visible labels. Placeholder text is never the label.
- Required, invalid, help, and error text is connected with `aria-describedby`/`aria-invalid`.
- On failed submit, focus moves to the error summary; links in the summary focus the field.
- Dialog/Sheet focus is trapped and restored through Radix behavior and verified tests.
- Status updates use a restrained `aria-live="polite"` region; persistent errors use an alert role.
- Reachability, flow direction, charge/credit, active plan, and enabled state never rely only on color.
- Tables retain captions and header associations. Horizontal overflow regions are keyboard reachable and labeled.
- Chart information has a complete non-graphical equivalent.
- Motion respects reduced-motion. Theme follows stored preference; there is no unrequested animated transition.
- Focus indicators are at least 2 CSS px equivalent and maintain contrast in every theme.
- Touch targets are at least 44 by 44 CSS px where spacing allows.
- At 200% zoom and a 320 px viewport, no page loses controls or information; only intentionally scrollable data regions may overflow horizontally.

### 12.2 Data formatting

- Use `Intl.NumberFormat` with explicit units and consistent maximum fractions matching current precision.
- Display dates in Australian day/month/year form while retaining ISO values in data and machine-readable attributes.
- Display timezone context near ambiguous timestamps.
- Use a Unicode minus sign or explicit “credit” label consistently, without changing the underlying sign.
- Never announce `null` money as zero; say “Unpriced”.

## 13. FastAPI static-build serving plan

### 13.1 Build

Add a Node build stage to Docker only during the implementation phase:

1. copy `web/package.json` and lockfile;
2. run `npm ci`;
3. copy `web/` and run typecheck, tests, and `vite build`;
4. configure Vite base as `/static/webui/`;
5. copy `dist/` into `solarmax/static/webui/` in the final Python image;
6. keep the final image Python-only and continue running as the existing unprivileged user.

The current Compose port, volume, environment variables, SQLite path, and Uvicorn command remain unchanged. Add a container health check only as a separately reviewed operational enhancement; it is not required for UI parity.

### 13.2 Serve

- Keep the existing `/static` mount so `/static/webui/assets/<hash>` works without another server.
- For each existing page route, return `solarmax/static/webui/index.html` when the React rollout switch is enabled; otherwise render the existing Jinja template.
- Keep `/api/*`, `/docs`, `/openapi.json`, and static files outside any SPA catch-all.
- Prefer explicit handling for the five known paths over a broad catch-all. Unknown URLs should remain real 404s.
- Set the index response to no-cache and fingerprinted assets to immutable caching.
- Preserve the current page URLs so bookmarks and direct refreshes work.
- Do not embed installation data into `index.html`; all runtime data comes from same-origin APIs.

### 13.3 Development

Vite runs only in development. Its proxy forwards `/api` to FastAPI. Direct testing must also run against the FastAPI-served production build to catch base-path, MIME, caching, and direct-route failures. CORS is not required for production because the build is same-origin.

## 14. Dependency justification and constraints

| Dependency | Decision | Justification/constraint |
|---|---|---|
| React 19 / React DOM | Adopt | Component/state model for five interactive pages and shared status/forms; no SSR runtime required. |
| TypeScript | Adopt strict mode | The current wire shapes contain nullable and inconsistent unions where compile-time exhaustiveness materially reduces display errors. |
| Vite | Adopt | Fast static build, code splitting, deterministic hashed assets, and a simple FastAPI base-path configuration. |
| Tailwind CSS v4 | Adopt | Token-driven responsive styling with minimal authored global CSS; semantic variables remain the source of truth. |
| shadcn/ui | Adopt selectively | Repository-owned component source avoids a black-box theme layer. Do not install the catalog wholesale. |
| Radix primitives | Adopt per component | Focus management and accessible behavior for Sheet, Dialog, Select, Tabs, Menu, and Tooltip. |
| Lucide React | Adopt | One coherent, tree-shakeable icon vocabulary. Icons supplement text and are imported individually. |
| Recharts | Adopt conditionally | Existing signed daily series justifies a responsive divergent bar chart; lazy-load and bundle/a11y gates apply. |
| React Router | Adopt | Five preserved direct URLs, nested shell, active navigation, route errors, and URL-owned filters justify a mature router. |
| openapi-typescript | Adopt as dev dependency | Generates the frontend boundary once FastAPI response schemas are explicit. |
| Vitest + Testing Library + user-event | Adopt as dev dependencies | Fast component, hook, and accessibility-behavior tests. |
| MSW | Adopt as dev dependency | Deterministic API state/error/latency tests without duplicating fetch internals. |
| Playwright + axe integration | Adopt as dev dependencies | Full-route responsive, keyboard, contrast/semantics, direct-load, and production-build checks. |

Do not add a global state library, form library, date library, animation library, second icon set, second component library, or second chart library initially. Native React form state plus focused helpers is enough at current scale; revisit only with measured complexity.

Dependency versions must be pinned by the lockfile and checked against supported Node/Python build images at implementation time. This planning document intentionally does not guess future patch versions.

## 15. Testing strategy

### 15.1 Backend and contract

- Keep all 24 current tests passing with `python -m pytest -q`.
- Add route contract tests for every GET/POST, form field, redirect, JSON negotiation path, error code, and normalized response.
- Snapshot OpenAPI structurally and run generated-TypeScript drift checks.
- Test no-plan, missing-ID, invalid settings, invalid TOU, weather timeout/error, and chart-day bounds.
- Test `live_observed_at` with multiple inverters and unavailable aggregates.
- Add MCP smoke tests around every existing tool after any response-model work.

### 15.2 Frontend unit/integration

- Formatters: money, credits, null/unpriced, dates, units.
- API adapters: SQLite integer flags, sparse legacy bill union, field errors, followed HTML fallback.
- Polling hook: visibility pause, abort, retry, out-of-order suppression, configured cadence.
- Every form: initial values, dirty state, keyboard submit, pending state, success, field/global errors.
- Energy flow: zero, positive channel, unavailable, reduced motion.
- Chart: charge/credit, empty, tooltip labels, data-table equivalence.
- TOU editor: 30-minute values, `24:00`, add/remove/reorder, JSON round-trip, overlap/gap warnings.

### 15.3 End-to-end

Run Playwright against the FastAPI-served production build with seeded isolated data:

- direct load and navigation for all five URLs;
- all-reachable, partially unreachable, no enabled inverter, and no-reading states;
- add/edit inverter and plan;
- edit TOU using structured and JSON modes;
- settings/theme/mode/active-plan changes;
- recommendation success and upstream failure;
- bill rows for charge, credit, fixed, and unpriced reconciliation;
- close-day confirmation;
- manual poll pending/result behavior;
- keyboard-only completion of core workflows;
- 320, 768, 1024, and wide desktop viewports;
- four themes, forced colors, reduced motion, and 200% zoom;
- axe checks with no serious/critical violations.

### 15.4 Packaging and performance

- Build the Docker image from a clean checkout.
- Assert no Node executable or source dependency tree is present in the final runtime layer.
- Smoke `/`, all four other page routes, `/api/state`, `/openapi.json`, and one hashed asset.
- Verify correct base paths and no stale index caching.
- Set initial performance budgets after measuring the scaffold on target hardware. Proposed starting gates: initial route JavaScript at or below 180 KiB gzip, lazy chart chunk at or below 160 KiB gzip, and no single unexpected dependency dominating the initial chunk.
- Test usable shell and text within 2 seconds on a throttled mid-tier mobile profile; hardware polling latency is reported separately and is not hidden as frontend load time.

## 16. Migration plan and concrete tasks

### Phase 0 — Freeze and harden the contract

Tasks:

1. Add golden request/response fixtures for all current routes using an isolated database.
2. Add explicit Pydantic response models.
3. Normalize the no-plan bill response additively.
4. Add `GET /api/plans/{plan_id}/tou`.
5. Add defined `live_observed_at` metadata.
6. Add JSON content negotiation to mutations while retaining form redirects.
7. Normalize known API errors and missing-ID behavior.
8. Add MCP regression tests and preserve existing service calls.
9. Decide and document whether billing-cycle filtering is corrected in this migration or clearly deferred.

Billing-cycle decision: filter the current bill to the configured cycle window; expose `billing_window_applied: true` in the normalized bill response for an active plan. This keeps the Billing and Overview tallies on the same calculation.

Gate:

- all legacy tests and new contract tests pass;
- existing Jinja pages and form submissions remain byte/behavior compatible where contract fixtures require it;
- OpenAPI fully describes the successful JSON responses and validation errors;
- no database migration is required merely to render React.

### Phase 1 — Frontend foundation and dual serving

Tasks:

1. Scaffold `web/` with React 19, strict TypeScript, Vite, Tailwind v4, lint/typecheck/test scripts, and lockfile.
2. Generate the API types and add drift CI.
3. Implement the typed fetch client, domain adapters, formatters, and request status model.
4. Establish semantic tokens and all four themes.
5. Add only the selected shadcn/Radix primitives and Lucide imports.
6. Implement React Router and the responsive app shell.
7. Add the Vite production base path and multi-stage build.
8. Add a FastAPI rollout switch that can serve React or legacy Jinja for the five page routes.
9. Add production-build smoke tests.

Gate:

- the empty shell directly loads at all five routes from FastAPI;
- API/static routes are never intercepted;
- legacy UI remains the default and fully functional;
- keyboard navigation, skip link, focus styles, mobile navigation, themes, and cache headers pass.

### Phase 2 — Overview parity

Tasks:

1. Implement dashboard state hook and visibility-aware API refresh.
2. Implement reachability/unavailable states and conservative freshness labels.
3. Implement exact live/today metrics and energy-flow topology.
4. Implement manual Poll inverters workflow.
5. Implement bill summary, inverter health, and Recharts chart with data fallback.
6. Add unit, integration, end-to-end, responsive, and accessibility coverage.

Gate:

- every dashboard value and state matches backend fixtures;
- partial/stale/simulated data cannot be displayed as live;
- chart wording matches its actual source;
- poll completion does not claim hardware success without refreshed reachability;
- bundle gates pass.

### Phase 3 — Inverters and Settings parity

Tasks:

1. Build inverter master/detail, edit, and add workflows.
2. Build recommendation preview and explicit re-check/apply workflow.
3. Build grouped settings form and theme preview/revert.
4. Implement inline/global validation, pending/success states, and dirty guards.
5. Document profile-only versus hardware-control semantics in the UI.

Gate:

- all current fields round-trip without loss;
- unchecked booleans serialize correctly;
- add/edit works and unknown IDs are not silent successes;
- recommendation errors cannot alter a profile;
- all four themes pass AA checks on both routes.

### Phase 4 — Plans, TOU, and Billing parity

Tasks:

1. Build plan list/add/edit and active-plan behavior.
2. Build structured TOU editor, coverage warnings, and Advanced JSON round-trip.
3. Build billing summary, filters, audit table, unpriced states, and close-day action.
4. Verify money precision/signs and timezone display against backend fixtures.
5. Add full regression coverage for replacement saves and reconciliation rows.

Gate:

- every existing plan/TOU field can be read and saved;
- persisted schedules round-trip without automatic mutation;
- removed periods require an explicit save/confirmation path;
- null amounts are never included as zero in a complete total claim;
- no-plan and unpriced cases are first-class;
- billing-cycle copy is truthful to backend behavior.

### Phase 5 — Cutover and observation

Tasks:

1. Make React selectable in staging and run parity sessions with representative SQLite fixtures.
2. Compare route/API logs, error rates, poll duration, weather failures, and client errors without logging sensitive payloads.
3. Enable React by default while retaining the legacy switch.
4. Validate Docker volume persistence, direct URLs, cold cache, upgrades, and rollback.
5. Update README/operator notes only in the implementation change set.

Gate:

- all automated gates pass from a clean checkout;
- no P0/P1 accessibility or data-integrity defect remains;
- operators can switch back to Jinja without database changes or image rebuild;
- production observation window completes with agreed error/performance thresholds.

### Phase 6 — Legacy removal

Tasks:

1. Remove legacy templates, CSS, and JavaScript only after at least two stable releases or the team's agreed observation period.
2. Remove the rollout switch and legacy-specific contract fallback code.
3. Retain legacy form API behavior if external clients still depend on it.
4. Re-run repository-wide dead-code, dependency, security, and image-size audits.

Gate:

- rollback is available by deploying the prior known-good image;
- no monitored traffic or documented workflow depends on Jinja assets;
- all API/MCP compatibility tests remain green.

## 17. Rollback strategy

### During dual-run phases

- A server-side environment/config switch selects `legacy` or `react` page rendering at process start.
- Both implementations use the same service and database, so switching does not migrate or copy user data.
- New API fields/endpoints are additive and harmless to the legacy UI.
- If React assets are missing or their manifest fails startup validation, fail safely to legacy rendering and log a non-sensitive deployment error.

### After React becomes default

- Keep the Jinja templates and legacy static files in the image for the observation period.
- Roll back first by toggling page rendering, then by deploying the previous image if backend contract changes are implicated.
- Do not roll back the SQLite file for UI defects. Contract hardening must be backward compatible and should avoid destructive schema changes.
- Keep hashed assets from one prior React build during deployment or use atomic image replacement so an old cached index never points at unavailable chunks.

### After legacy removal

- Rollback is image-based to the last known-good release using the same `/data` volume.
- Any later database migration must have its own forward-compatible rollback assessment; it is outside this UI-only plan.

## 18. Acceptance gates for final release

The React UI is complete only when all of the following are true:

### Functional parity

- All five existing URLs and all pre-existing API paths still work.
- Every current form field, action, status, table value, theme, and hidden API capability has an intentional replacement or documented non-UI status.
- Add inverter and Add plan are genuinely exposed and tested.
- TOU periods are readable without HTML scraping and save through the canonical backend.
- Manual poll, recommendation, close-day, settings, plan, TOU, and inverter actions report truthful outcomes.
- MCP tools produce compatible results.

### Data integrity and truthfulness

- Unavailable is never shown as zero; stale is never shown as live.
- Multi-inverter aggregation matches the service.
- Money remains cents across the API, credits keep their sign, and unknown pricing stays unknown.
- The UI performs no billing, meter, tier, timezone, or energy-balance business calculation.
- Chart and billing labels describe their actual data scope.
- No install-specific value is bundled into static assets or emitted into client logs.

### Accessibility and responsive behavior

- Core workflows are complete with keyboard and screen reader.
- Automated scans have no serious/critical violations; manual focus, zoom, reduced-motion, and contrast checks pass in four themes.
- The interface works at 320 px through wide desktop without lost actions/data.
- Every chart has a complete tabular/text alternative.

### Reliability and delivery

- Python, frontend, E2E, OpenAPI drift, Docker build, and production smoke suites pass from a clean checkout.
- Static caching and direct-route loading are correct.
- Initial/lazy bundle budgets and the agreed target-hardware usability threshold pass.
- React can be disabled without database change during the observation period.
- The final runtime contains no Node server and remains an unprivileged FastAPI container.

## 19. Risks, blockers, and decisions still required

### Current blockers to full implementation

1. **TOU read gap:** React cannot reproduce the Plans page from JSON until an additive endpoint or state expansion exists.
2. **Untyped responses:** current OpenAPI response schemas are empty, so generated types are not yet authoritative.
3. **No-plan bill union:** sparse and full bill responses differ materially.
4. **Freshness gap:** the aggregate state has no capture timestamp.
5. **Redirect-only mutations:** robust SPA success/error behavior needs JSON negotiation or new additive JSON endpoints.
6. **Weather errors:** upstream failures lack a stable client contract.
7. **Billing scope:** configured billing windows are not enforced by the current summary.
8. **Chart scope:** chart points do not always reconcile to the displayed bill total.
9. **Record identity:** some unknown IDs silently succeed.
10. **Network security:** no authentication or CSRF protection exists. Do not broaden network exposure as part of the UI deployment without an explicit security decision.

### Implementation decisions to record before Phase 1

- whether generated `dist/` assets are built only in Docker/CI or committed;
- the rollout-switch name/default and observation duration;
- whether billing-window correctness is in scope before cutover;
- whether API content negotiation or additive `/api/v2` mutations gives the clearest maintainable contract;
- exact measured JavaScript budgets after the minimal scaffold;
- supported browser floor and target low-power display hardware;
- whether Add inverter/plan should be allowed for all deployments despite the existing API already supporting it.

None of these decisions requires changing the core service or inventing frontend data. Phase 0 resolves the contract uncertainties; subsequent phases can then implement the complete application with a controlled, reversible path.
