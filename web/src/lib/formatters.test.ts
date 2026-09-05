import { describe, expect, it } from "vitest";
import { formatAuDate, formatEnergy, formatMoney, formatPower } from "./formatters";
describe("formatters", () => { it("does not turn absent values into zero", () => { expect(formatMoney(null)).toBe("Unpriced"); expect(formatEnergy(null)).toBe("—"); expect(formatPower(null)).toBe("—"); }); it("formats operational values for Australia", () => { expect(formatMoney(-125)).toMatch(/^-.*1\.25/); expect(formatEnergy(1.2)).toContain("1.2 kWh"); expect(formatAuDate("2026-09-05")).toBe("05/09/2026"); }); });
