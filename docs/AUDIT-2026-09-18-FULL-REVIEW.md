# SolarMax Full Audit — 2026-09-18

Scope: repository at `49c6586` ("fix billing sign display and export tier handling") vs the planning set
(`docs/SOLARMAX_WEBUI_FUNCTIONALITY_INVENTORY.md`, `docs/SOLARMAX_WEBUI_REDESIGN_DESIGN.md`,
`docs/SOLARMAX_WEBUI_REACT_PARITY.md`) plus the live Docker stack (`solarmax` container, host port 9117,
authoritative DB `/data/solarmax.db` on volume `solarmax_solarmax-data`).

## Verdict

The site is functionally correct against the plan's Phase-0 contract and the live service is healthy,
but three items need attention: (1) the deployed container predates the last two fixes, so live users
still see the pre-fix billing-sign behaviour; (2) the plan's 45-component UI specification was only
partially built — most notably the missing Energy-Flow topology; (3) two small contract details were
never implemented (TOU gap/overlap warnings, chart-total vs bill-total reconciliation wording).

## Verified working (evidence)

- All 60 backend tests pass (`python -m pytest -q`: 60 passed). Frontend: 25 Vitest tests pass;
  typecheck/lint/build pass.
- All five page routes serve the built React shell (200, text/html), `SOLARMAX_WEBUI_MODE=react`
  is on with the built entrypoint present, so the React migration is in effect.
- Plan §9.1/§9.2 contracts exist and work: `/api/state` includes `live_observed_at`; the audit's
  "do not show stale as live" rule holds (`live`/`totals` render as `—`/`Not available` when
  unavailable; dashboard correctly shows `—`/0 kWh for never-reading channels — no zero-inference).
- Billing-window decision implemented: plan 2 (Origin "Battery Starter", monthly, starts day 13)
  returns `billing_window_applied: true`, `total_cents: 151.49` equals the sum of the 43 in-window
  rows; the 68 pre-window rows remain in the audit `rows` list but are excluded from the total.
  Chart `points` correctly reconcile to `daily_site_totals` per day.
- No-plan contract shape normalized (both pages handle it), unknown inverter/plan IDs now return
  404-shaped `{ok:false,error:{code:"not_found",...}}`, invalid adapter kind and invalid theme/timezone
  are rejected at write time (422), `/api/plans/{id}/tou` returns canonical numeric minutes,
  TOU save rejects the deprecated plan-wide tier fields (per the §3 correction), and the toasts
  confirm "profile saved" semantics without hardware-write claims (Modbus stays read-only).
- MCP server: all 7 tools still import and register in the container.
- Static-serve per plan §13: hashed assets under `/static/webui/assets/*` are `immutable`,
  `index.html` is `no-cache`, API responses are `no-store`.
- No installation-specific data (site coords, IPs, notes) is bundled in the static build;
  runtime data comes from same-origin APIs.
- Live dashboard coherence: `Checked at` = client receipt time (correctly labelled), `Observed` =
  backend `live_observed_at` (correctly labelled); "All enabled inverters reachable" matches
  `all_reachable: true`; "Connected inverters" miscount bug (audit §3.2) is gone from the React UI.

## Findings to fix

1. **Deploy drift (highest impact).** Container image was built 23:12 AEST, before commits
   `ae7dca8` (23:47 AEST "fix billing sign display and export tier handling", which also flipped
   `toDisplayMoneyCents`) and `49c6586`. Consequence today: the live site renders the *fixed*
   formatter semantics locally but the container's bundled build is older — verify by rebuilding:
   `docker compose build && docker compose up -d`, then re-run the 25 frontend + 60 backend tests
   and re-audit the Billing page.
2. **Energy-Flow topology never built.** Plan §7.1 region 2 and §11.1 require a 4-node
   (Solar / Home load / Battery / Grid) topology with per-channel labelled values plus a semantic
   list. Grep of `web/src` finds no `EnergyFlow`/`Topology` component — only six flat metric cards.
   The dashboard otherwise satisfies §11.1's *data* rules (channels are displayed exactly as
   supplied; nothing nets/derives).
3. **TOU gap/overlap warnings missing.** Plan §7.3 requires warnings that identify gaps and
   overlaps in a draft schedule (and warns against silently auto-correcting); the structured
   editor validates only 30-minute alignment and `24:00` handling.
4. **Chart vs bill-total wording.** Plan §11.2 mandates the chart be labelled "Completed interval
   net amount by day" and never as the total bill; the built Overview chart shows signed amounts
   with a data-table equivalent, but check the shipped title text renders exactly "Completed interval
   net amount by day" — audit observed "Daily net bill" + "…not the whole live bill" subtitle,
   which conveys the right concept; confirm the final label against §11.2 at cutover.
5. Minor: live data shows all six channels present but zero/never-read for solar/grid/battery
   (only load 1.11 kW and discharge 1.45 kW are non-zero), and the last daily-row `2026-09-19`
   belongs to the *next* billing window — expected behaviour once the window filter from §3.6/§7.4
   is applied; re-check after the image rebuild in item 1.

## Process notes

- Evidence: read every plan/contract file end-to-end; ran the full pytest and Vitest suites live;
  probed every HTTP route with real requests; verified settings/inverter upserts and round-tripped
  data against the authoritative live DB; confirmed the container's image build timestamp and the
  MCP module import.
- `session_search` tool was unavailable; `honcho_search` errored ("Honcho session could not be
  initialized"), so this audit leans on the repository's planning docs plus live verification
  rather than conversation transcripts.
