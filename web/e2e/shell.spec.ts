import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const routes = [
  ["/", "Overview", "Poll inverters"],
  ["/inverters", "Inverters", "Add inverter"],
  ["/plans", "Plans & TOU", "Add plan"],
  ["/billing", "Billing", "Refresh"],
  ["/settings", "Settings", "Save settings"],
] as const;

for (const [path, heading, action] of routes) {
  test(`renders the ${heading} route with its live control surface`, async ({ page }) => {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
    await expect(page.getByRole("button", { name: action, exact: true })).toBeVisible();
    await expect(page.locator("main")).toBeVisible();
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations).toEqual([]);
  });
}

test("provides an accessible compact navigation shell", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
  await page.getByRole("link", { name: "Billing" }).last().click();
  await expect(page).toHaveURL(/\/billing$/);
  await expect(page.getByRole("heading", { name: "Billing", level: 1 })).toBeVisible();
});

test("exposes labelled TOU editor controls and settings selectors", async ({ page }) => {
  await page.goto("/plans");
  await expect(page.getByRole("button", { name: "Save TOU schedule" })).toBeVisible();
  await expect(page.getByLabel(/Direction for/).first()).toBeVisible();
  await page.goto("/settings");
  await expect(page.getByLabel("Theme")).toBeVisible();
  await expect(page.getByLabel("Active plan")).toBeVisible();
});

test("keeps tablet navigation links named and exposes the active state", async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 768 });
  await page.goto("/billing");
  const billing = page.getByRole("link", { name: "Billing", exact: true });
  await expect(billing).toHaveAttribute("aria-label", "Billing");
  await expect(billing).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("link", { name: "Overview", exact: true })).toHaveAttribute("aria-label", "Overview");
});

test("lays out Overview energy measurements as separate six-card groups and wraps without overflow", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");

  const energySections = page.locator(".section").filter({ has: page.getByRole("heading", { name: /^(Live energy|Today)$/ }) });
  await expect(energySections).toHaveCount(2);
  for (const section of await energySections.all()) {
    const cards = section.locator(".overview-metric-grid > .metric");
    await expect(cards).toHaveCount(6);
    await expect(cards).toHaveText([/Solar generation/, /Load use/, /Grid import/, /Grid export/, /Battery charge/, /Battery discharge/]);
    await expect(section.locator(".overview-metric-grid")).toHaveCSS("grid-template-columns", /.+ .+ .+ .+ .+ .+/);
    await expect(section.locator(".overview-metric-grid")).toHaveCSS("gap", "12px");
    await expect(cards.first()).toHaveCSS("border-top-style", "solid");
    await expect(cards.first()).toHaveCSS("border-top-width", "1px");
    await expect(cards.first()).toHaveCSS("border-top-left-radius", "8px");
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("keeps daily net bill columns fixed, centered, baseline-aligned, and non-scrolling", async ({ page }) => {
  const points = Array.from({ length: 14 }, (_, index) => ({
    day: `2026-09-${String(index + 1).padStart(2, "0")}`,
    amount_cents: [80, 1234, -5678, 240, 3456, -890, 100, 4567, -1200, 75, 2300, -999, 150, 6789][index],
  }));
  await page.route("**/api/chart**", async (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ points }),
  }));
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");

  const chart = page.getByRole("img", { name: /Daily net bill chart/ });
  await expect(chart).toBeVisible();
  const desktop = await chart.evaluate((node) => {
    const columns = [...node.querySelectorAll<HTMLElement>(".chart-column")];
    const bars = [...node.querySelectorAll<HTMLElement>(".chart-bar")];
    const dates = [...node.querySelectorAll<HTMLElement>(".chart-date")];
    const amounts = [...node.querySelectorAll<HTMLElement>(".chart-amount")];
    const areas = [...node.querySelectorAll<HTMLElement>(".chart-bar-area")];
    const width = (element: HTMLElement) => element.getBoundingClientRect().width;
    const center = (element: HTMLElement) => element.getBoundingClientRect().left + width(element) / 2;
    const columnWidth = width(columns[0]);
    return {
      columnWidths: columns.map(width),
      barWidths: bars.map(width),
      chartGap: columns[1].getBoundingClientRect().left - columns[0].getBoundingClientRect().right,
      columnWidth,
      labelCenters: columns.map((_, index) => [center(columns[index]), center(dates[index]), center(amounts[index])]),
      baselines: areas.map((area) => area.getBoundingClientRect().bottom),
      dates: dates.map((date) => date.textContent),
      labels: amounts.map((amount) => amount.textContent),
      labelOverflow: [...dates, ...amounts].some((label) => label.scrollWidth > label.clientWidth),
      pageOverflow: document.documentElement.scrollWidth > window.innerWidth,
      chartOverflow: node.scrollWidth > node.clientWidth,
    };
  });

  expect(new Set(desktop.columnWidths.map(Math.round)).size).toBe(1);
  expect(new Set(desktop.barWidths.map(Math.round)).size).toBe(1);
  expect(desktop.columnWidth).toBeCloseTo(34, 0);
  expect(desktop.barWidths[0]).toBeCloseTo(34, 0);
  expect(desktop.chartGap).toBeCloseTo(20.8, 1);
  expect(desktop.labelCenters.flat().every((value, index, values) => index % 3 === 0 || Math.abs(value - values[index - index % 3]) < 0.5)).toBe(true);
  expect(Math.max(...desktop.baselines) - Math.min(...desktop.baselines)).toBeLessThan(0.5);
  expect(desktop.dates.at(-1)).toBe("14/09");
  expect(desktop.labels.at(-1)).toBe("$67.89");
  expect(desktop.labels.length).toBeLessThan(points.length);
  expect(desktop.labelOverflow).toBe(false);
  expect(desktop.pageOverflow).toBe(false);
  expect(desktop.chartOverflow).toBe(false);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => chart.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
  const mobile = await chart.evaluate((node) => ({
    dates: [...node.querySelectorAll<HTMLElement>(".chart-date")].map((date) => date.textContent),
    labels: [...node.querySelectorAll<HTMLElement>(".chart-amount")].map((amount) => amount.textContent),
    pageOverflow: document.documentElement.scrollWidth > window.innerWidth,
    chartOverflow: node.scrollWidth > node.clientWidth,
  }));
  expect(mobile.dates.at(-1)).toBe("14/09");
  expect(mobile.labels.at(-1)).toBe("$67.89");
  expect(mobile.labels.length).toBeLessThan(points.length);
  expect(mobile.pageOverflow).toBe(false);
  expect(mobile.chartOverflow).toBe(false);
});
