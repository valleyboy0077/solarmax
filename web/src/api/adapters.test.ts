import { describe, expect, it } from "vitest";
import type { components } from "./generated";
import { dashboardSnapshot } from "./adapters";

describe("dashboardSnapshot", () => {
  it("accepts the generated dashboard response and preserves typed fields", () => {
    const response: components["schemas"]["DashboardStateResponse"] = {
      settings: { theme: "deep-ocean", mode: "manual", poll_interval_seconds: 30, site_name: "Home", site_lat: 0, site_lon: 0, site_timezone: "Australia/Brisbane" },
      inverters: [], power_plans: [], live: null, totals: null, live_observed_at: null, all_reachable: true,
      bill: { plan: null, total_cents: 0, today_grid_import_kwh: 0, today_grid_export_kwh: 0, supply_charge_cents: 0, supply_charge_days: 0, billing_window_applied: false },
      theme: "deep-ocean",
    };
    expect(dashboardSnapshot(response)).toEqual({ settings: response.settings, all_reachable: true, live_observed_at: null });
  });
});
