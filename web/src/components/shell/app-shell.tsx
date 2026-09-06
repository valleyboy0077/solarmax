import { Menu, RefreshCw, Settings2, SunMedium, BatteryCharging, ClipboardList, ReceiptText, LayoutDashboard } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useState } from "react";
import { useDashboardPoll } from "../../hooks/use-dashboard-poll";
import { Button } from "../ui/button";

const navigation = [
  ["/", "Overview", LayoutDashboard], ["/inverters", "Inverters", BatteryCharging], ["/plans", "Plans & TOU", ClipboardList], ["/billing", "Billing", ReceiptText], ["/settings", "Settings", Settings2],
] as const;

function Navigation({ close }: { close?: () => void }) {
  return <nav aria-label="Primary navigation" className="nav-list">{navigation.map(([to, label, Icon]) => <NavLink aria-label={label} end={to === "/"} key={to} onClick={close} title={label} to={to} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}><Icon aria-hidden="true" size={18} /><span>{label}</span></NavLink>)}</nav>;
}

function MobileNavigation() {
  const [open, setOpen] = useState(false);
  return <Dialog.Root open={open} onOpenChange={setOpen}><Dialog.Trigger asChild><button className="icon-button mobile-menu" aria-label="Open navigation"><Menu aria-hidden="true" /></button></Dialog.Trigger><Dialog.Portal><Dialog.Overlay className="sheet-overlay" /><Dialog.Content className="sheet-content"><Dialog.Title className="brand">SolarMax</Dialog.Title><Navigation close={() => setOpen(false)} /><Dialog.Close asChild><Button>Close navigation</Button></Dialog.Close></Dialog.Content></Dialog.Portal></Dialog.Root>;
}

export function AppShell() {
  const { data, error, checking, checkedAt, refresh } = useDashboardPoll();
  const refreshEverything = async () => { await refresh(); window.dispatchEvent(new Event("solarmax:refresh")); };
  useEffect(() => { if (data) document.documentElement.dataset.theme = data.settings.theme; }, [data]);
  const status = checking ? "Checking…" : error ? "API refresh failed" : data?.all_reachable ? "All enabled inverters reachable" : "Telemetry unavailable";
  const statusClass = checking || (data?.all_reachable ?? false) ? "status" : "status danger";
  return <div className="app-frame"><a className="skip-link" href="#main-content">Skip to content</a><aside className="rail"><div className="brand"><SunMedium aria-hidden="true" size={20} /> SolarMax</div><Navigation /><p className="rail-mode">{data?.settings ? `${data.settings.site_name} · operational` : "Operational interface"}</p></aside><header className="topbar"><MobileNavigation /><div><strong>{data?.settings.site_name ?? "SolarMax"}</strong><span className={statusClass}>{status}</span></div><div className="top-actions"><span className="checked-at">{checkedAt ? `Checked at ${checkedAt.toLocaleTimeString("en-AU")}` : "Awaiting API"}</span><Button onClick={() => void refreshEverything()} disabled={checking}><RefreshCw aria-hidden="true" size={16} /> Refresh data</Button></div></header>{error && <div className="stale-banner" role="alert">API refresh failed. The displayed shell may be out of date.</div>}<main id="main-content" className="content"><Outlet /></main><div aria-live="polite" className="sr-only">{status}</div></div>;
}
