import { describe, expect, it } from "vitest";
import { dashboardSnapshot } from "./adapters";
describe("dashboardSnapshot", () => { it("maps a Phase 0 response without inferring truthiness", () => { expect(dashboardSnapshot({ settings: { theme: "deep-ocean", site_name: "Home", poll_interval_seconds: 30 }, all_reachable: 1, live_observed_at: null }).all_reachable).toBe(false); }); });
