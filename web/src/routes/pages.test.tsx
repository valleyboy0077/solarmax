import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useState } from "react";
import type { components } from "../api/generated";
import { Chart, BillingRoute, DashboardRoute, EndTimeField, PlansRoute, StartTimeField, TouTableColumnGroup, TouTierInputs } from "./pages";
import { getFittingChartPointCount } from "./chart-layout";

const apiMocks = vi.hoisted(() => ({ apiDelete: vi.fn(), apiGet: vi.fn(), apiForm: vi.fn(), apiPost: vi.fn() }));
vi.mock("../api/client", () => apiMocks);

const appCss = readFileSync(resolve(process.cwd(), "src/styles/app.css"), "utf8");

afterEach(() => { cleanup(); apiMocks.apiDelete.mockReset(); apiMocks.apiGet.mockReset(); apiMocks.apiForm.mockReset(); apiMocks.apiPost.mockReset(); vi.restoreAllMocks(); });

function plan(id: number, provider: string, name: string): components["schemas"]["PowerPlanResponse"] {
  return { id, provider_name: provider, plan_name: name, billing_cycle: "monthly", billing_start_day: 1, billing_start_month: 1, daily_supply_charge_cents: 12, export_tier_kwh: 0, export_tier_rate_cents_per_kwh: 0, export_excess_rate_cents_per_kwh: 0, notes: "" };
}

function tou(planId: number, label: string): components["schemas"]["TouPeriodsResponse"] {
  return { plan_id: planId, periods: [{ id: planId * 10, plan_id: planId, direction: "import", label, start_minute: 0, end_minute: 30, rate_cents_per_kwh: 20, export_tier_kwh: 0, export_tier_rate_cents_per_kwh: 0, export_excess_rate_cents_per_kwh: 0 }] };
}

function mockPlans(plans: components["schemas"]["PowerPlanResponse"][], activePlanId: number | null) {
  const state = {
    settings: { theme: "classic-dark", mode: "manual", poll_interval_seconds: 30, site_name: "Solarmax", site_lat: -27.4698, site_lon: 153.0251, site_timezone: "Australia/Brisbane", active_plan_id: activePlanId },
    inverters: [], power_plans: plans, live: null, totals: null, live_observed_at: null, all_reachable: true,
    bill: { plan: plans[0] ?? null, total_cents: 0, rows: [], daily: [], daily_site_totals: [], today_grid_import_kwh: 0, today_grid_export_kwh: 0, supply_charge_cents: 0, supply_charge_days: 0, billing_window_applied: false },
    theme: "classic-dark",
  } as components["schemas"]["DashboardStateResponse"];
  const touByPath = new Map(plans.map((item) => [`/api/plans/${item.id}/tou`, tou(item.id, `${item.plan_name} import`)]));
  apiMocks.apiGet.mockImplementation((path: string) => path === "/api/state" ? Promise.resolve(state) : Promise.resolve(touByPath.get(path)));
}

describe("Billing page contract", () => {
  it("renders the active plan name in a labelled field", async () => {
    const bill = {
      plan: {
        id: 1, provider_name: "Provider", plan_name: "Future Saver", billing_cycle: "monthly",
        billing_start_day: 1, billing_start_month: 1, daily_supply_charge_cents: 0,
        export_tier_kwh: 0, export_tier_rate_cents_per_kwh: 0, export_excess_rate_cents_per_kwh: 0, notes: "",
      },
      total_cents: 0, rows: [], daily: [], daily_site_totals: [], today_grid_import_kwh: 0,
      today_grid_export_kwh: 0, supply_charge_cents: 0, supply_charge_days: 0, billing_window_applied: false,
    } satisfies components["schemas"]["BillSummaryResponse"];
    apiMocks.apiGet.mockResolvedValueOnce(bill);

    render(<BillingRoute />);

    expect(await screen.findByText("Active plan")).toBeInTheDocument();
    expect(screen.getByText("Future Saver")).toBeInTheDocument();
    expect(screen.queryByText("Supply charge")).not.toBeInTheDocument();
  });

  it("shows one amount row per day with TOU brackets and no rates", async () => {
    apiMocks.apiGet.mockResolvedValueOnce({
      plan: { id: 1, provider_name: "Provider", plan_name: "Future Saver", billing_cycle: "monthly", billing_start_day: 1, billing_start_month: 1, daily_supply_charge_cents: 0, export_tier_kwh: 0, export_tier_rate_cents_per_kwh: 0, export_excess_rate_cents_per_kwh: 0, notes: "" },
      total_cents: 1234, rows: [
        { day: "2026-09-01", period_label: "Off-peak", direction: "import", kwh: 2, rate_cents_per_kwh: 10, amount_cents: 200, unpriced: false },
        { day: "2026-09-01", period_label: "Peak", direction: "import", kwh: 1, rate_cents_per_kwh: 30, amount_cents: 300, unpriced: false },
        { day: "2026-09-02", period_label: "Off-peak", direction: "import", kwh: 3, rate_cents_per_kwh: 10, amount_cents: 300, unpriced: false },
      ], daily: [], daily_site_totals: [], today_grid_import_kwh: 0, today_grid_export_kwh: 0, supply_charge_cents: 0, supply_charge_days: 0, billing_window_applied: false,
    } satisfies components["schemas"]["BillSummaryResponse"]);

    render(<BillingRoute />);

    const table = await screen.findByRole("table", { name: "Billing amounts by day and TOU bracket" });
    expect(table.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(table).toHaveTextContent("Off-peak");
    expect(table).toHaveTextContent("Peak");
    expect(table).toHaveTextContent("-$2.00");
    expect(table).toHaveTextContent("-$3.00");
    expect(table).toHaveTextContent("Total");
    expect(table).toHaveTextContent("Day date");
    expect(table).toHaveTextContent("Total for day");
    expect(table.querySelectorAll("thead th")).toHaveLength(5);
    expect(table.querySelectorAll("tbody tr")[0]).toHaveTextContent("-$5.00");
    expect(table.querySelectorAll("tbody tr")[1]).toHaveTextContent("-$3.00");
    expect(table).not.toHaveTextContent("c/kWh");
    expect(table).not.toHaveTextContent("Energy");
  });

  it("adds duplicate-label TOU intervals instead of dropping all but the last", async () => {
    apiMocks.apiGet.mockResolvedValueOnce({
      plan: { id: 2, provider_name: "Origin", plan_name: "Battery Starter", billing_cycle: "monthly", billing_start_day: 13, billing_start_month: 9, daily_supply_charge_cents: 149, export_tier_kwh: 0, export_tier_rate_cents_per_kwh: 0, export_excess_rate_cents_per_kwh: 0, notes: "" },
      total_cents: 0,
      rows: [
        { day: "2026-09-18", period_label: "Off-peak", direction: "export", kwh: 15.21, rate_cents_per_kwh: 3, amount_cents: -45.63, unpriced: false },
        { day: "2026-09-18", period_label: "Off-peak", direction: "export", kwh: 0.01, rate_cents_per_kwh: 3, amount_cents: -0.03, unpriced: false },
        { day: "2026-09-18", period_label: "Peak", direction: "export", kwh: 8.76, rate_cents_per_kwh: 28, amount_cents: -245.28, unpriced: false },
      ],
      daily: [], daily_site_totals: [], today_grid_import_kwh: 0, today_grid_export_kwh: 23.98, supply_charge_cents: 149, supply_charge_days: 1, billing_window_applied: true,
    } satisfies components["schemas"]["BillSummaryResponse"]);

    render(<BillingRoute />);

    const table = await screen.findByRole("table", { name: "Billing amounts by day and TOU bracket" });
    expect(table.querySelectorAll("thead th")).toHaveLength(5);
    expect(table.querySelector("tbody tr"),).toHaveTextContent("$0.46");
    expect(table.querySelector("tbody tr"),).toHaveTextContent("$2.45");
    expect(table.querySelector("tbody tr"),).toHaveTextContent("$2.91");
  });

  it("presents mixed import, export, and fixed rows with user-facing signs", async () => {
    apiMocks.apiGet.mockResolvedValueOnce({
      plan: { id: 3, provider_name: "Origin", plan_name: "Mixed Saver", billing_cycle: "monthly", billing_start_day: 1, billing_start_month: 1, daily_supply_charge_cents: 179, export_tier_kwh: 0, export_tier_rate_cents_per_kwh: 0, export_excess_rate_cents_per_kwh: 0, notes: "" },
      total_cents: -21,
      rows: [
        { day: "2026-09-19", period_label: "Import", direction: "import", kwh: 1, rate_cents_per_kwh: 100, amount_cents: 100, unpriced: false },
        { day: "2026-09-19", period_label: "Export", direction: "export", kwh: 1, rate_cents_per_kwh: 300, amount_cents: -300, unpriced: false },
        { day: "2026-09-19", period_label: "Supply", direction: "fixed", kwh: 1, rate_cents_per_kwh: null, amount_cents: 179, unpriced: false },
      ],
      daily: [],
      daily_site_totals: [{ day: "2026-09-19", plan_id: 3, plan_name: "Mixed Saver", solar_kwh: 0, load_kwh: 0, grid_import_kwh: 1, grid_export_kwh: 1, battery_charge_kwh: 0, battery_discharge_kwh: 0, daily_bill_amount_cents: -21 }],
      today_grid_import_kwh: 1,
      today_grid_export_kwh: 1,
      supply_charge_cents: 179,
      supply_charge_days: 1,
      billing_window_applied: true,
    } satisfies components["schemas"]["BillSummaryResponse"]);

    const { container } = render(<BillingRoute />);

    const audit = await screen.findByRole("table", { name: "Billing amounts by day and TOU bracket" });
    expect(audit).toHaveTextContent("-$1.00");
    expect(audit).toHaveTextContent("$3.00");
    expect(audit).toHaveTextContent("-$1.79");
    expect(audit).toHaveTextContent("$0.21");
    const dailyTotals = container.querySelector('[aria-label="Daily site totals and billing"]')!;
    expect(dailyTotals).toHaveTextContent("$0.21");
    const dailyHeaders = Array.from(dailyTotals.querySelectorAll("thead th"));
    expect(dailyHeaders.map((cell) => cell.textContent)).toEqual([
      "Date", "Solar Gen", "Load Use kWh", "Grid ImportOff-peak", "Grid ImportPeak",
      "Grid ExportOff-peak", "Grid ExportPeak", "Batt Charge kWh", "Batt Discharge kWh",
      "Power Plan", "Daily Amount",
    ]);
    expect(dailyHeaders.slice(3, 7).map((cell) => Array.from(cell.children, (line) => [line.tagName, line.textContent]))).toEqual([
      [["SPAN", "Grid Import"], ["BR", ""], ["SPAN", "Off-peak"]],
      [["SPAN", "Grid Import"], ["BR", ""], ["SPAN", "Peak"]],
      [["SPAN", "Grid Export"], ["BR", ""], ["SPAN", "Off-peak"]],
      [["SPAN", "Grid Export"], ["BR", ""], ["SPAN", "Peak"]],
    ]);
    const dailyCells = dailyTotals.querySelectorAll("tbody td");
    expect(dailyCells).toHaveLength(11);
    expect(Array.from(dailyCells).slice(3, 7).every((cell) => cell.textContent === "—" && cell.getAttribute("aria-label") === "Not available")).toBe(true);
  });
});

describe("Overview billing cards", () => {
  it("presents current bill and supply charge with user-facing signs", async () => {
    const activePlan = { ...plan(4, "Origin", "Mixed Saver"), daily_supply_charge_cents: 179 };
    const state = {
      settings: { theme: "classic-dark", mode: "manual", poll_interval_seconds: 30, site_name: "Solarmax", site_lat: -27.4698, site_lon: 153.0251, site_timezone: "Australia/Brisbane", active_plan_id: 4 },
      inverters: [], power_plans: [activePlan], live: null, totals: null, live_observed_at: null, all_reachable: true,
      bill: { plan: activePlan, total_cents: -21, rows: [], daily: [], daily_site_totals: [], today_grid_import_kwh: 1, today_grid_export_kwh: 1, supply_charge_cents: 179, supply_charge_days: 1, billing_window_applied: true },
      theme: "classic-dark",
    } satisfies components["schemas"]["DashboardStateResponse"];
    apiMocks.apiGet.mockImplementation((path: string) => path === "/api/state"
      ? Promise.resolve(state)
      : Promise.resolve({ points: [] } satisfies components["schemas"]["ChartResponse"]));

    const { container } = render(<DashboardRoute />);

    await screen.findByRole("heading", { name: "Current bill" });
    expect(container.querySelector(".bill-total")).toHaveTextContent("$0.21");
    expect(container.querySelector(".summary-list")).toHaveTextContent("-$1.79 × 1 day");
  });
});

describe("Overview live energy panel", () => {
  it("places the live Battery level panel above Battery discharge without the Zap icon", async () => {
    const state = {
      settings: { theme: "classic-dark", mode: "manual", poll_interval_seconds: 30, site_name: "Solarmax", site_lat: -27.4698, site_lon: 153.0251, site_timezone: "Australia/Brisbane" },
      inverters: [], power_plans: [], live: { solar_kw: 0, load_kw: 1.05, grid_import_kw: 0.05, grid_export_kw: 0, battery_charge_kw: 0, battery_discharge_kw: 1.41, battery_level_percent: 73.4 }, totals: null, live_observed_at: "2026-09-19T03:58:54+10:00", all_reachable: true,
      bill: { plan: null, total_cents: 0, rows: [], daily: [], daily_site_totals: [], today_grid_import_kwh: 0, today_grid_export_kwh: 0, supply_charge_cents: 0, supply_charge_days: 0, billing_window_applied: false },
      theme: "classic-dark",
    } satisfies components["schemas"]["DashboardStateResponse"];
    apiMocks.apiGet.mockImplementation((path: string) => path === "/api/state"
      ? Promise.resolve(state)
      : Promise.resolve({ points: [] } satisfies components["schemas"]["ChartResponse"]));

    render(<DashboardRoute />);

    const livePanel = (await screen.findByRole("heading", { name: "Live energy" })).closest("section")!;
    const levelPanel = livePanel.querySelector(".live-energy-level .metric")!;
    const metrics = livePanel.querySelector(".overview-metric-grid")!;
    const dischargePanel = within(metrics as HTMLElement).getByText("Battery discharge").closest(".metric")!;
    expect(levelPanel).toHaveTextContent("Battery level");
    expect(levelPanel).toHaveTextContent("73.4%");
    expect(levelPanel.compareDocumentPosition(dischargePanel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(livePanel.querySelector("svg")).toBeNull();
    expect(appCss).toMatch(/\.live-energy-layout\s*\{[^}]*grid-template-columns:repeat\(6,minmax\(0,1fr\)\);[^}]*gap:12px;/);
    expect(appCss).toMatch(/\.live-energy-layout > \.section-heading\s*\{\s*grid-column:1 \/ 6;/);
    expect(appCss).toMatch(/\.live-energy-level\s*\{\s*grid-column:6/);
  });
});

describe("Plans & TOU tabs", () => {
  it("renders named tabs and pairs the selected plan with its TOU editor", async () => {
    const plans = [plan(1, "Provider One", "Solar Saver"), plan(2, "Provider Two", "Night Saver")];
    mockPlans(plans, 2);
    const user = userEvent.setup();
    render(<PlansRoute />);

    expect(await screen.findAllByRole("tab")).toHaveLength(2);
    expect(screen.getByRole("tab", { name: "Solar Saver" })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("tab", { name: "Night Saver" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByLabelText("Edit TOU for")).not.toBeInTheDocument();
    expect(await screen.findByLabelText("Direction for Night Saver import")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Show time picker" })).toHaveLength(2);
    expect(document.querySelectorAll('input[type="time"]')).toHaveLength(0);
    const startInput = screen.getByLabelText("Start time for Night Saver import");
    expect(startInput).toHaveClass("tou-start-time-input");
    expect(startInput).toHaveClass("tou-time-control");
    const endControl = document.querySelector(".tou-end-time-control");
    expect(endControl).toHaveClass("tou-time-control");
    const rateInput = screen.getByLabelText("Rate for Night Saver import in cents per kilowatt hour");
    expect(rateInput).toHaveClass("tou-rate-input");
    expect(rateInput.closest("td")).toHaveClass("tou-rate-cell");

    await user.click(screen.getByRole("tab", { name: "Solar Saver" }));

    expect(screen.getByRole("tab", { name: "Solar Saver" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("form", { name: "Solar Saver settings" })).toBeInTheDocument();
    expect(await screen.findByLabelText("Direction for Solar Saver import")).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Night Saver settings" })).not.toBeInTheDocument();
    expect(apiMocks.apiGet).toHaveBeenCalledWith("/api/plans/1/tou");
  });

  it("saves edits for the selected plan", async () => {
    const selectedPlan = plan(1, "Provider One", "Solar Saver");
    mockPlans([selectedPlan], 1);
    apiMocks.apiForm.mockResolvedValue({ ok: true, redirect_to: "/plans", resource_id: 1 });
    const user = userEvent.setup();
    render(<PlansRoute />);

    await screen.findByRole("form", { name: "Solar Saver settings" });
    await user.clear(screen.getByLabelText("Provider"));
    await user.type(screen.getByLabelText("Provider"), "Updated Provider");
    await user.click(screen.getByRole("button", { name: "Save plan" }));

    await waitFor(() => expect(apiMocks.apiForm).toHaveBeenCalledWith("/api/plans", expect.objectContaining({ plan_id: 1, provider_name: "Updated Provider", plan_name: "Solar Saver" })));
  });

  it("requires confirmation before deleting a plan and reports delete errors", async () => {
    const plans = [plan(1, "Provider One", "Solar Saver"), plan(2, "Provider Two", "Night Saver")];
    mockPlans(plans, 1);
    apiMocks.apiDelete.mockRejectedValue(new Error("The power plan has billing history and cannot be deleted"));
    const confirmMock = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<PlansRoute />);

    await screen.findByRole("tab", { name: "Night Saver" });
    await user.click(screen.getByRole("tab", { name: "Night Saver" }));
    await screen.findByRole("form", { name: "Night Saver settings" });
    await user.click(screen.getByRole("button", { name: "Delete plan" }));
    expect(confirmMock).toHaveBeenCalledWith("Delete Provider Two — Night Saver? This cannot be undone.");
    expect(apiMocks.apiDelete).not.toHaveBeenCalled();

    confirmMock.mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "Delete plan" }));
    await waitFor(() => expect(apiMocks.apiDelete).toHaveBeenCalledWith("/api/plans/2"));
    expect(await screen.findByRole("alert")).toHaveTextContent("The power plan has billing history and cannot be deleted");
  });
});

describe("Daily net bill chart", () => {
  it("chooses the newest points that fit a measured chart width", () => {
    expect(getFittingChartPointCount(250, 14)).toBe(4);
    expect(getFittingChartPointCount(1000, 14)).toBe(14);
    expect(getFittingChartPointCount(0, 14)).toBe(0);
  });

  it("reslices the visual chart on resize while retaining every table row", () => {
    const points = Array.from({ length: 14 }, (_, index) => ({
      day: `2026-09-${String(index + 1).padStart(2, "0")}`,
      amount_cents: index * 100,
    })) satisfies components["schemas"]["ChartResponse"]["points"];
    const originalClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");
    let chartWidth = 250;
    let notifyResize: (() => void) | undefined;
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => chartWidth });
    class TestResizeObserver {
      constructor(callback: ResizeObserverCallback) {
        notifyResize = () => callback([], this as unknown as ResizeObserver);
      }
      observe() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", TestResizeObserver);

    try {
      const { container } = render(<Chart points={points} />);
      const chart = screen.getByRole("img", { name: /Daily net bill chart/ });
      expect(chart.querySelectorAll(".chart-column")).toHaveLength(4);
      expect(chart).not.toHaveTextContent("01/09");
      expect(chart).toHaveTextContent("14/09");
      expect(container.querySelectorAll(".chart-data tbody tr")).toHaveLength(14);

      chartWidth = 1000;
      act(() => notifyResize?.());
      expect(chart.querySelectorAll(".chart-column")).toHaveLength(14);
    } finally {
      if (originalClientWidth) Object.defineProperty(HTMLElement.prototype, "clientWidth", originalClientWidth);
      else delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth;
      vi.unstubAllGlobals();
    }
  });

  it("inverts raw accounting signs in chart bars, labels, and the data table", () => {
    const points = [
      { day: "2026-09-01", amount_cents: 100 },
      { day: "2026-09-02", amount_cents: -300 },
      { day: "2026-09-03", amount_cents: 179 },
    ] satisfies components["schemas"]["ChartResponse"]["points"];
    const { container } = render(<Chart points={points} />);

    const chart = screen.getByRole("img", { name: /Daily net bill chart/ });
    const columns = [...chart.querySelectorAll(".chart-column")];
    expect(columns).toHaveLength(3);
    expect(chart.querySelectorAll(".chart-bar-area")).toHaveLength(3);
    expect(chart.querySelectorAll(".chart-bar")).toHaveLength(3);
    expect(chart.querySelectorAll(".chart-date")).toHaveLength(3);
    expect(chart.querySelectorAll(".chart-amount")).toHaveLength(3);
    const bars = [...chart.querySelectorAll(".chart-bar")];
    expect(bars[0]).toHaveClass("chart-bar", "negative");
    expect(bars[1]).toHaveClass("chart-bar", "positive", "credit");
    expect(bars[2]).toHaveClass("chart-bar", "negative");
    expect(chart).toHaveTextContent("-$1.00");
    expect(chart).toHaveTextContent("$3.00");
    expect(chart).toHaveTextContent("-$1.79");
    expect(screen.getByText("01/09")).toBeInTheDocument();
    expect(screen.getByText("02/09")).toBeInTheDocument();
    expect(screen.getByText("03/09")).toBeInTheDocument();
    for (const column of columns) {
      expect(column.children[0]).toHaveClass("chart-bar-area");
      expect(column.children[1]).toHaveClass("chart-date");
      expect(column.children[2]).toHaveClass("chart-amount");
    }
    expect(container.querySelector(".chart-bar.credit")).toHaveAttribute("title", "02/09/2026 $3.00");
    expect(container.querySelectorAll(".chart-amount")[0]).toHaveAttribute("title", "-$1.00");
    expect(container.querySelectorAll(".chart-data tbody tr")).toHaveLength(3);
    expect(container.querySelector(".chart-data tbody")).toHaveTextContent("03/09/2026");
    expect(container.querySelector(".chart-data tbody")).toHaveTextContent("-$1.79");
    expect(appCss).toMatch(/\.chart-bar\.negative\s*\{\s*background:var\(--danger\);\s*\}/);
    expect(appCss).toMatch(/\.chart-bar\.positive\s*\{\s*background:var\(--success\);\s*\}/);
  });
});

describe("TOU export tier inputs", () => {
  it("renders the three tier fields only for export rows", () => {
    const update = vi.fn();
    const exportPeriod = {
      id: 1, plan_id: 1, direction: "export" as const, label: "Daytime solar",
      start_minute: 540, end_minute: 960, rate_cents_per_kwh: 3,
      export_tier_kwh: 8, export_tier_rate_cents_per_kwh: 8,
      export_excess_rate_cents_per_kwh: 3,
    };
    const { rerender } = render(<table><tbody><tr><TouTierInputs period={exportPeriod} update={update} /></tr></tbody></table>);
    expect(screen.getByLabelText("Tier allowance for Daytime solar")).toBeInTheDocument();
    expect(screen.getByLabelText("Tier-one rate for Daytime solar")).toBeInTheDocument();
    expect(screen.getByLabelText("Excess rate for Daytime solar")).toBeInTheDocument();

    rerender(<table><tbody><tr><TouTierInputs period={{ ...exportPeriod, direction: "import" }} update={update} /></tr></tbody></table>);
    expect(screen.queryByLabelText("Tier allowance for Daytime solar")).not.toBeInTheDocument();
    expect(screen.getByLabelText("No export tiers for Daytime solar")).toBeInTheDocument();
  });
});

describe("TOU end time control", () => {
  const period = {
    id: 1, plan_id: 1, direction: "import" as const, label: "Overnight",
    start_minute: 1320, end_minute: 1440, rate_cents_per_kwh: 20,
    export_tier_kwh: 0, export_tier_rate_cents_per_kwh: 0, export_excess_rate_cents_per_kwh: 0,
  };

  it("uses the same controlled picker for Start and End with the exact clock affordance", async () => {
    const user = userEvent.setup();
    render(<div><StartTimeField period={period} update={vi.fn()} /><EndTimeField period={period} update={vi.fn()} /></div>);
    const pickerButtons = screen.getAllByRole("button", { name: "Show time picker" });
    expect(pickerButtons).toHaveLength(2);
    for (const pickerButton of pickerButtons) {
      expect(pickerButton).toHaveAttribute("title", "Show time picker");
      expect(pickerButton).toHaveClass("tou-time-picker-button");
      expect(pickerButton.querySelector(".tou-time-picker-icon")).toBeInTheDocument();
    }
    expect(document.querySelectorAll('input[type="time"]')).toHaveLength(0);

    await user.click(pickerButtons[0]);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(pickerButtons[1]);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("offers 00 through 24 hours and only 00 and 30 minutes", async () => {
    const user = userEvent.setup();
    render(<EndTimeField period={period} update={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Show time picker" }));
    const hour = screen.getByRole("combobox", { name: "Hour" });
    const minute = screen.getByRole("combobox", { name: "Minute" });
    const hourOptions = [...hour.querySelectorAll("option")].map((option) => option.textContent);
    const minuteOptions = [...minute.querySelectorAll("option")].map((option) => option.textContent);
    expect(hourOptions).toHaveLength(25);
    expect(hourOptions).toContain("00");
    expect(hourOptions).toContain("24");
    expect(minuteOptions).toEqual(["00", "30"]);
  });

  it("prevents the invalid 24:30 combination", async () => {
    const update = vi.fn();
    const user = userEvent.setup();
    function ControlledField() {
      const [endMinute, setEndMinute] = useState(1410);
      const updateValue = (value: number) => { update(value); setEndMinute(value); };
      return <EndTimeField period={{ ...period, end_minute: endMinute }} update={updateValue} />;
    }
    render(<ControlledField />);
    await user.click(screen.getByRole("button", { name: "Show time picker" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Hour" }), "24");
    const minute = screen.getByRole("combobox", { name: "Minute" });
    expect(minute).toHaveValue("00");
    expect(screen.getByRole("option", { name: "30" })).toBeDisabled();
    expect(update).toHaveBeenLastCalledWith(1440);
    await user.selectOptions(minute, "30");
    expect(update).toHaveBeenLastCalledWith(1440);
  });

  it("updates the field when a picker value is selected", async () => {
    const user = userEvent.setup();
    function ControlledField() {
      const [endMinute, setEndMinute] = useState(0);
      return <EndTimeField period={{ ...period, end_minute: endMinute }} update={setEndMinute} />;
    }
    render(<ControlledField />);
    await user.click(screen.getByRole("button", { name: "Show time picker" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Hour" }), "08");
    await user.selectOptions(screen.getByRole("combobox", { name: "Minute" }), "30");
    expect(screen.getByRole("textbox", { name: "End time for Overnight" })).toHaveValue("08:30");
  });

  it("displays and accepts the end-of-day value 24:00", async () => {
    const update = vi.fn();
    const user = userEvent.setup();
    render(<EndTimeField period={{ ...period, end_minute: 0 }} update={update} />);
    const endInput = screen.getByRole("textbox", { name: "End time for Overnight" });

    await user.clear(endInput);
    await user.type(endInput, "24:00");

    expect(endInput).toHaveValue("24:00");
    expect(update).toHaveBeenLastCalledWith(1440);

    await user.clear(endInput);
    await user.type(endInput, "24:30");
    expect(update).toHaveBeenLastCalledWith(1440);
  });
});

describe("TOU table layout", () => {
  it("keeps the eight editor fields and actions in explicit columns", () => {
    const { container } = render(<table><TouTableColumnGroup /></table>);
    expect(container.querySelectorAll("colgroup > col")).toHaveLength(9);
    expect(container.querySelector(".tou-direction-column")).toBeInTheDocument();
    expect(container.querySelector(".tou-label-column")).toBeInTheDocument();
    expect(container.querySelector(".tou-actions-column")).toBeInTheDocument();
  });
});
