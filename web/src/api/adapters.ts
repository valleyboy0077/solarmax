import type { components } from "./generated";

type DashboardStateResponse = components["schemas"]["DashboardStateResponse"];
export type AppTheme = DashboardStateResponse["settings"]["theme"];
export type DashboardSnapshot = Pick<DashboardStateResponse, "settings" | "all_reachable" | "live_observed_at">;

export function dashboardSnapshot(data: DashboardStateResponse): DashboardSnapshot {
  const theme = data.settings?.theme;
  if (!theme) throw new Error("Invalid dashboard theme response.");
  return { settings: data.settings, all_reachable: data.all_reachable, live_observed_at: data.live_observed_at };
}
