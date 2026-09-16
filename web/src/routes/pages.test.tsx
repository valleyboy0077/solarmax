import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import type { components } from "../api/generated";
import { Chart, BillingRoute, EndTimeField, PlansRoute, TouTableColumnGroup, TouTierInputs } from "./pages";
import { getFittingChartPointCount } from "./chart-layout";

const apiMocks = vi.hoisted(() => ({ apiDelete: vi.fn(), apiGet: vi.fn(), apiForm: vi.fn(), apiPost: vi.fn() }));
vi.mock("../api/client", () => apiMocks);

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
    expect(screen.getByRole("button", { name: "Show time picker" })).toBeInTheDocument();

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

  it("renders equal chart columns with exact dollar labels and the date-above-amount order", () => {
    const points = [
      { day: "2026-09-01", amount_cents: 80 },
      { day: "2026-09-02", amount_cents: 1234 },
      { day: "2026-09-03", amount_cents: -5678 },
    ] satisfies components["schemas"]["ChartResponse"]["points"];
    const { container } = render(<Chart points={points} />);

    const chart = screen.getByRole("img", { name: /Daily net bill chart/ });
    const columns = [...chart.querySelectorAll(".chart-column")];
    expect(columns).toHaveLength(3);
    expect(chart.querySelectorAll(".chart-bar-area")).toHaveLength(3);
    expect(chart.querySelectorAll(".chart-bar")).toHaveLength(3);
    expect(chart.querySelectorAll(".chart-date")).toHaveLength(3);
    expect(chart.querySelectorAll(".chart-amount")).toHaveLength(3);
    expect(chart).toHaveTextContent("$0.80");
    expect(chart).toHaveTextContent("$12.34");
    expect(chart).toHaveTextContent("-$56.78");
    expect(screen.getByText("01/09")).toBeInTheDocument();
    expect(screen.getByText("02/09")).toBeInTheDocument();
    expect(screen.getByText("03/09")).toBeInTheDocument();
    for (const column of columns) {
      expect(column.children[0]).toHaveClass("chart-bar-area");
      expect(column.children[1]).toHaveClass("chart-date");
      expect(column.children[2]).toHaveClass("chart-amount");
    }
    expect(container.querySelector(".chart-bar.credit")).toHaveAttribute("title", "03/09/2026 -$56.78");
    expect(container.querySelectorAll(".chart-amount")[0]).toHaveAttribute("title", "$0.80");
    expect(container.querySelectorAll(".chart-data tbody tr")).toHaveLength(3);
    expect(container.querySelector(".chart-data tbody")).toHaveTextContent("03/09/2026");
    expect(container.querySelector(".chart-data tbody")).toHaveTextContent("-$56.78");
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

  it("uses an exact clock affordance and opens the native picker", async () => {
    const showPicker = vi.fn();
    const originalShowPicker = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "showPicker");
    Object.defineProperty(HTMLInputElement.prototype, "showPicker", { configurable: true, value: showPicker });
    try {
      const user = userEvent.setup();
      render(<EndTimeField period={period} update={vi.fn()} />);
      const pickerButton = screen.getByRole("button", { name: "Show time picker" });
      expect(pickerButton).toHaveAttribute("title", "Show time picker");
      expect(screen.queryByText("Pick")).not.toBeInTheDocument();
      expect(document.querySelectorAll(".tou-end-time-control > input:not(.tou-picker-input)")).toHaveLength(1);
      expect(document.querySelector(".tou-picker-input")).toHaveAttribute("aria-hidden", "true");

      await user.click(pickerButton);
      expect(showPicker).toHaveBeenCalledTimes(1);
    } finally {
      if (originalShowPicker) Object.defineProperty(HTMLInputElement.prototype, "showPicker", originalShowPicker);
      else Reflect.deleteProperty(HTMLInputElement.prototype, "showPicker");
    }
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
