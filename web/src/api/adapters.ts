export type AppTheme = "classic-light" | "classic-dark" | "deep-ocean" | "ember-core";
export type DashboardSnapshot = { settings: { theme: AppTheme; site_name: string; poll_interval_seconds: number }; all_reachable: boolean; live_observed_at: string | null };

export function dashboardSnapshot(value: unknown): DashboardSnapshot {
  const data = value as { settings?: { theme?: AppTheme; site_name?: string; poll_interval_seconds?: number }; all_reachable?: boolean; live_observed_at?: string | null };
  const theme = data.settings?.theme;
  if (!theme || !["classic-light", "classic-dark", "deep-ocean", "ember-core"].includes(theme)) throw new Error("Invalid dashboard theme response.");
  return { settings: { theme, site_name: data.settings?.site_name ?? "SolarMax", poll_interval_seconds: data.settings?.poll_interval_seconds ?? 30 }, all_reachable: data.all_reachable === true, live_observed_at: data.live_observed_at ?? null };
}
