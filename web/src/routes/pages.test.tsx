import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "../api/generated";
import { Chart, TouTableColumnGroup, TouTierInputs } from "./pages";
import { getFittingChartPointCount } from "./chart-layout";

afterEach(cleanup);

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

describe("TOU table layout", () => {
  it("keeps the eight editor fields and actions in explicit columns", () => {
    const { container } = render(<table><TouTableColumnGroup /></table>);
    expect(container.querySelectorAll("colgroup > col")).toHaveLength(9);
    expect(container.querySelector(".tou-direction-column")).toBeInTheDocument();
    expect(container.querySelector(".tou-label-column")).toBeInTheDocument();
    expect(container.querySelector(".tou-actions-column")).toBeInTheDocument();
  });
});
