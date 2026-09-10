import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TouTableColumnGroup, TouTierInputs } from "./pages";

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
