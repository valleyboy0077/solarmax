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
