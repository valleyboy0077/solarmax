import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
for (const path of ["/", "/inverters", "/plans", "/billing", "/settings"]) test(`serves the Phase 1 shell at ${path}`, async ({ page }) => { await page.goto(path); await expect(page.locator("h1")).toBeVisible(); const results = await new AxeBuilder({ page }).analyze(); expect(results.violations).toEqual([]); });
