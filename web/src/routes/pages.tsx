import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Check, CircleAlert, Clock, CloudSun, LoaderCircle, Pencil, Plus, RefreshCw, Save, Sparkles, Trash2 } from "lucide-react";
import { apiDelete, apiForm, apiGet, apiPost } from "../api/client";
import type { components } from "../api/generated";
import { formatAuDate, formatEnergy, formatMoney, formatPower, toDisplayMoneyCents } from "../lib/formatters";
import { Button } from "../components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "../components/ui/states";
import { getFittingChartPointCount } from "./chart-layout";

type State = components["schemas"]["DashboardStateResponse"];
type Bill = components["schemas"]["BillSummaryResponse"];
type Inverter = components["schemas"]["InverterResponse"];
type Plan = components["schemas"]["PowerPlanResponse"];
type Tou = components["schemas"]["TouPeriodResponse"];
type FormFields = Record<string, string | number | boolean | null | undefined>;

function useResource<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true);
    try { setData(await apiGet<T>(path)); setError(null); }
    catch (cause) { setError(cause as Error); }
    finally { setLoading(false); }
  }, [path]);
  useEffect(() => {
    void load();
    const refreshAll = () => void load();
    window.addEventListener("solarmax:refresh", refreshAll);
    const interval = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, 30_000);
    return () => { window.removeEventListener("solarmax:refresh", refreshAll); window.clearInterval(interval); };
  }, [load]);
  return { data, error, loading, reload: load };
}

function Page({ title, description, actions, children }: { title: string; description: string; actions?: ReactNode; children: ReactNode }) {
  return <section className="page"><header className="page-header"><div><h1>{title}</h1><p>{description}</p></div>{actions && <div className="page-actions">{actions}</div>}</header>{children}</section>;
}

function Notice({ message, error }: { message: string | null; error?: boolean }) {
  return message ? <p className={`feedback ${error ? "feedback-error" : "feedback-success"}`} role={error ? "alert" : "status"}>{error ? <CircleAlert size={17} /> : <Check size={17} />} {message}</p> : null;
}

function statusFor(inverter: Inverter) {
  if (!inverter.enabled) return ["Disabled", "neutral"] as const;
  return inverter.reachable ? ["Reachable", "ok"] as const : ["Unreachable", "warn"] as const;
}
function Status({ inverter }: { inverter: Inverter }) { const [label, variant] = statusFor(inverter); return <span className={`badge ${variant}`}>{label}</span>; }

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) { return <div className={`metric ${tone ?? ""}`}><span>{label}</span><strong>{value}</strong></div>; }
function formatBatteryLevel(value: number | null | undefined) { return typeof value === "number" ? `${new Intl.NumberFormat("en-AU", { maximumFractionDigits: 1, minimumFractionDigits: 1 }).format(value)}%` : "—"; }
function TodayBatteryPanel({ minimum, maximum, coverage }: { minimum: number | null | undefined; maximum: number | null | undefined; coverage?: components["schemas"]["BatterySocCoverageResponse"] | null }) {
  const complete = coverage ? coverage.coverage_status === "complete" : typeof minimum === "number" && typeof maximum === "number";
  const minimumValue = coverage?.min_percent ?? minimum;
  const maximumValue = coverage?.display_max_percent ?? maximum;
  const maximumSource = coverage?.display_max_percent_source ?? (typeof maximumValue === "number" ? "observed" : "unavailable");
  const display = (value: number | null | undefined) => formatBatteryLevel(value);
  const coverageMessage = coverage?.coverage_status === "partial"
    ? "Observed SOC extrema so far; full-day coverage is partial."
    : "SOC extrema are unavailable because no valid samples were observed.";
  const provenanceMessage = maximumSource === "derived_from_grid_export_off_peak"
    ? null
    : !complete ? coverageMessage : null;
  return <div className="metric today-battery-panel" aria-label="Today battery SOC"><span>Battery SOC</span><div className="today-battery-values"><div><span>Minimum SOC</span><strong aria-label={display(minimumValue) === "—" ? "Not available" : undefined}>{display(minimumValue)}</strong></div><div><span>Maximum SOC</span><strong aria-label={display(maximumValue) === "—" ? "Not available" : undefined}>{display(maximumValue)}</strong></div></div>{provenanceMessage && <p className="today-battery-message">{provenanceMessage}</p>}</div>;
}
function MutationButton({ label, path, fields = {}, onSuccess, primary = false }: { label: string; path: string; fields?: FormFields; onSuccess?: () => void | Promise<void>; primary?: boolean }) {
  const [pending, setPending] = useState(false); const [message, setMessage] = useState<string | null>(null);
  const invoke = async () => { setPending(true); setMessage(null); try { await apiForm(path, fields); await onSuccess?.(); setMessage("Completed successfully."); } catch (cause) { setMessage((cause as Error).message); } finally { setPending(false); } };
  return <span className="mutation"><Button variant={primary ? "primary" : "secondary"} onClick={() => void invoke()} disabled={pending}>{pending ? <LoaderCircle className="spin" size={16} /> : null}{label}</Button><Notice message={message} error={message !== "Completed successfully."} /></span>;
}

function BillTable({ bill }: { bill: Bill }) {
  const rows = bill.rows ?? [];
  if (!rows.length) return <EmptyState title="No bill lines yet">There are no completed billable intervals for the active plan.</EmptyState>;

  const columns = [...new Map(rows.map((row) => {
    const key = `${row.direction}:${row.period_label}`;
    return [key, { key, label: row.period_label, direction: row.direction }];
  })).values()];
  const days = [...new Set(rows.map((row) => row.day))];
  // Multiple TOU rows can legitimately share a label, such as off-peak
  // 00:00–16:00 and 21:00–24:00. Aggregate them for the audit column;
  // Map(rows.map(...)) would silently retain only the last interval.
  const amounts = new Map<string, NonNullable<Bill["rows"]>[number]>();
  for (const row of rows) {
    const key = `${row.day}:${row.direction}:${row.period_label}`;
    const existing = amounts.get(key);
    if (!existing) {
      amounts.set(key, { ...row });
      continue;
    }
    amounts.set(key, {
      ...existing,
      kwh: existing.kwh + row.kwh,
      amount_cents: existing.amount_cents === null || row.amount_cents === null
        ? existing.amount_cents ?? row.amount_cents
        : existing.amount_cents + row.amount_cents,
      unpriced: existing.unpriced || row.unpriced,
    });
  }
  const dailyTotals = new Map(days.map((day) => {
    const dayRows = rows.filter((row) => row.day === day && row.amount_cents !== null);
    return [day, dayRows.length ? dayRows.reduce((total, row) => total + (row.amount_cents ?? 0), 0) : null];
  }));

  return <div className="bill-audit-table-wrap" tabIndex={0}><table className="bill-audit-table" aria-label="Billing amounts by day and TOU bracket"><thead><tr><th>Day date</th><th>Power plan</th>{columns.map((column) => <th key={column.key} title={`${column.label} (${column.direction})`}>{column.label} {column.direction}</th>)}<th>Total for day</th></tr></thead><tbody>{days.map((day) => <tr key={day}>{<td>{formatAuDate(day)}</td>}<td>{bill.plan?.plan_name ?? "—"}</td>{columns.map((column) => { const row = amounts.get(`${day}:${column.direction}:${column.label}`); return <td className={row?.unpriced ? "unpriced" : ""} key={column.key}>{row ? formatMoney(row.amount_cents) : "—"}{row?.unpriced && <span className="unpriced-note">Unpriced</span>}</td>; })}<td className="bill-audit-total">{formatMoney(dailyTotals.get(day) ?? null)}</td></tr>)}</tbody></table></div>;
}

export function Chart({ points }: { points: components["schemas"]["ChartResponse"]["points"] }) {
  const chartRef = useRef<HTMLDivElement>(null);
  const [fittingPointCount, setFittingPointCount] = useState<number | null>(null);
  useLayoutEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const measure = () => {
      if (chart.clientWidth > 0) setFittingPointCount(getFittingChartPointCount(chart.clientWidth, points.length));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(chart);
    return () => observer.disconnect();
  }, [points.length]);

  if (!points.length) return <EmptyState title="No chart data yet">Poll the inverters to begin building completed billing history.</EmptyState>;
  const count = fittingPointCount === null ? points.length : Math.min(fittingPointCount, points.length);
  const visiblePoints = points.slice(Math.max(0, points.length - count));
  const max = Math.max(...visiblePoints.map((point) => Math.abs(point.amount_cents)), 1);
  return <><div ref={chartRef} className="chart" role="img" aria-label={`Daily net bill chart. Showing the most recent ${visiblePoints.length} of ${points.length} days. Each bar represents one day's charge or credit; dates and dollar amounts are shown below. Open the data table for exact values.`}>{visiblePoints.map((point) => { const date = formatAuDate(point.day); const exactAmount = formatMoney(point.amount_cents); const displayCents = toDisplayMoneyCents(point.amount_cents)!; const tone = displayCents < 0 ? "negative" : displayCents > 0 ? "positive credit" : "neutral"; return <div className="chart-column" key={point.day}><div className="chart-bar-area"><span aria-hidden="true" className={`chart-bar ${tone}`} style={{ height: `${Math.max(8, Math.abs(point.amount_cents) / max * 120)}px` }} title={`${date} ${exactAmount}`} /></div><small className="chart-date">{date.slice(0, 5)}</small><span className="chart-amount" title={exactAmount}>{exactAmount}</span></div>; })}</div><details className="chart-data"><summary>View chart data table</summary><div className="table-wrap"><table><thead><tr><th>Day</th><th>Net amount</th></tr></thead><tbody>{points.map((point) => <tr key={point.day}><td>{formatAuDate(point.day)}</td><td>{formatMoney(point.amount_cents)}</td></tr>)}</tbody></table></div></details></>;
}

export function DashboardRoute() {
  const state = useResource<State>("/api/state");
  const chart = useResource<components["schemas"]["ChartResponse"]>("/api/chart?days=14");
  if (state.loading && !state.data) return <LoadingState label="Loading site overview" />;
  if (state.error && !state.data) return <ErrorState>Unable to load the operational snapshot. <button className="link-button" onClick={() => void state.reload()}>Try again</button></ErrorState>;
  const data = state.data!; const live = data.live; const totals = data.totals; const unpriced = (data.bill.rows ?? []).filter((row) => row.unpriced).length;
  return <Page title="Overview" description="Current site operations, local-day energy and backend-calculated billing." actions={<MutationButton label="Poll inverters" path="/api/poll-now" onSuccess={async () => { await state.reload(); await chart.reload(); }} primary />}>
    {!data.all_reachable && <section className="alert-banner" role="alert"><CircleAlert size={18} /><div><strong>Telemetry unavailable</strong><br />Live and today totals are withheld until every enabled inverter has a current reading.</div></section>}
    <section className="status-strip"><div><span>Site</span><strong>{data.settings.site_name}</strong></div><div><span>Mode</span><strong>{data.settings.mode === "ai" ? "AI preference" : "Manual"}</strong></div><div><span>Poll cadence</span><strong>{data.settings.poll_interval_seconds}s</strong></div><div><span>Observed</span><strong>{data.live_observed_at ? new Date(data.live_observed_at).toLocaleString("en-AU") : "Not available"}</strong></div></section>
    <section className="section live-energy-section"><div className="live-energy-layout"><div className="section-heading"><div><h2>Live energy</h2><p>Aggregated across enabled inverters. Values are never inferred.</p></div></div><div className="live-energy-level"><Metric label="Battery level" value={formatBatteryLevel(live?.battery_level_percent)} tone="level" /></div><div className="metric-grid overview-metric-grid">{[["Solar generation", live?.solar_kw, "solar"], ["Load use", live?.load_kw, "load"], ["Grid import", live?.grid_import_kw, "import"], ["Grid export", live?.grid_export_kw, "export"], ["Battery charge", live?.battery_charge_kw, "charge"], ["Battery discharge", live?.battery_discharge_kw, "discharge"]].map(([label, value, tone]) => <Metric key={String(label)} label={String(label)} value={formatPower(typeof value === "number" ? value : null)} tone={String(tone)} />)}</div></div></section>
    <section className="section"><div className="today-layout"><div className="section-heading"><div><h2>Today</h2><p>Local-day counters: device registers for solar/load/battery; persisted plant-meter deltas for grid.</p></div></div><div className="today-battery-level"><TodayBatteryPanel minimum={totals?.battery_level_min_percent} maximum={totals?.battery_level_max_percent} coverage={data.today_battery_soc_coverage} /></div><div className="metric-grid overview-metric-grid">{[["Solar generation", totals?.solar_total_kwh, "solar"], ["Load use", totals?.load_total_kwh, "load"], ["Grid import", totals?.grid_import_total_kwh, "import"], ["Grid export", totals?.grid_export_total_kwh, "export"], ["Battery charge", totals?.battery_charge_total_kwh, "charge"], ["Battery discharge", totals?.battery_discharge_total_kwh, "discharge"]].map(([label, value, tone]) => <Metric key={String(label)} label={String(label)} value={formatEnergy(typeof value === "number" ? value : null)} tone={String(tone)} />)}</div></div></section>
    <div className="split-grid"><section className="section"><div className="section-heading"><div><h2>Daily net bill</h2><p>Completed interval amounts and eligible supply charges; not the whole live bill.</p></div></div>{chart.loading && !chart.data ? <LoadingState label="Loading chart" /> : chart.error ? <ErrorState>Chart unavailable. <button className="link-button" onClick={() => void chart.reload()}>Try again</button></ErrorState> : <Chart points={chart.data?.points ?? []} />}</section><section className="section"><div className="section-heading"><div><h2>Current bill</h2><p>{data.bill.plan ? `${data.bill.plan.provider_name} — ${data.bill.plan.plan_name}` : "No active plan"}</p></div></div><p className="bill-total">{formatMoney(data.bill.total_cents)}</p><dl className="summary-list"><div><dt>Imported today</dt><dd>{formatEnergy(data.bill.today_grid_import_kwh)}</dd></div><div><dt>Exported today</dt><dd>{formatEnergy(data.bill.today_grid_export_kwh)}</dd></div><div><dt>Supply charge</dt><dd>{formatMoney(data.bill.supply_charge_cents)} × {data.bill.supply_charge_days} day{data.bill.supply_charge_days === 1 ? "" : "s"}</dd></div></dl>{unpriced > 0 && <p className="warning-text">{unpriced} reconciliation line{unpriced === 1 ? " is" : "s are"} unpriced and excluded from money totals.</p>}<a className="text-link" href="/billing">Open billing audit</a></section></div>
    <section className="section"><div className="section-heading"><div><h2>Inverter health</h2><p>Profile status and configured operating-policy values.</p></div></div>{data.inverters.length ? <div className="inverter-list">{data.inverters.map((inverter) => <article className="inverter-row" key={inverter.id ?? inverter.name}><div><strong>{inverter.name}</strong><span>{inverter.model}</span></div><Status inverter={inverter} /><span>IP {inverter.ip_address || "not set"}</span><span>Reserve {inverter.battery_reserve_percent}%</span><span>Feed-in {inverter.battery_feed_in_limit_kw} kW</span></article>)}</div> : <EmptyState title="No inverter profiles">Add an inverter profile to configure site telemetry.</EmptyState>}</section>
  </Page>;
}

function InverterForm({ inverter, onSaved }: { inverter?: Inverter; onSaved: () => Promise<void> }) {
  const [pending, setPending] = useState(false); const [message, setMessage] = useState<string | null>(null);
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const f = new FormData(event.currentTarget); setPending(true); setMessage(null); try { await apiForm("/api/inverters", { inverter_id: inverter?.id, name: String(f.get("name") ?? ""), model: String(f.get("model") ?? ""), adapter_kind: String(f.get("adapter_kind") ?? ""), ip_address: String(f.get("ip_address") ?? ""), subnet: String(f.get("subnet") ?? ""), enabled: f.get("enabled") === "on", battery_feed_in_limit_kw: Number(f.get("battery_feed_in_limit_kw")), battery_reserve_percent: Number(f.get("battery_reserve_percent")), export_limit_kw: Number(f.get("export_limit_kw")), allow_grid_charge: f.get("allow_grid_charge") === "on", notes: String(f.get("notes") ?? "") }); await onSaved(); setMessage("Inverter saved."); } catch (cause) { setMessage((cause as Error).message); } finally { setPending(false); } };
  return <form className="form-card" onSubmit={(event) => void submit(event)}><div className="form-card-title"><h2>{inverter ? inverter.name : "Add inverter"}</h2>{inverter && <Status inverter={inverter} />}</div><div className="form-grid"><Field label="Name" name="name" defaultValue={inverter?.name ?? ""} required /><Field label="Model" name="model" defaultValue={inverter?.model ?? "SigenStor EC 20.0 TP AU"} required /><Field label="Adapter module" name="adapter_kind" defaultValue={inverter?.adapter_kind ?? "sigenstor_ec_20_0_tp_au"} required /><Field label="IP address" name="ip_address" defaultValue={inverter?.ip_address ?? ""} /><Field label="Subnet" name="subnet" defaultValue={inverter?.subnet ?? ""} /><Field label="Battery feed-in limit (kW)" name="battery_feed_in_limit_kw" type="number" step="0.1" defaultValue={inverter?.battery_feed_in_limit_kw ?? 0} /><Field label="Battery reserve (%)" name="battery_reserve_percent" type="number" min="0" max="100" defaultValue={inverter?.battery_reserve_percent ?? 20} /><Field label="Export limit (kW)" name="export_limit_kw" type="number" step="0.1" defaultValue={inverter?.export_limit_kw ?? 0} /><label className="field full"><span>Notes</span><textarea name="notes" defaultValue={inverter?.notes ?? ""} /></label><label className="check"><input name="allow_grid_charge" type="checkbox" defaultChecked={inverter?.allow_grid_charge ?? false} /> Allow grid charge</label><label className="check"><input name="enabled" type="checkbox" defaultChecked={inverter?.enabled ?? true} /> Enabled</label></div><Button type="submit" variant="primary" disabled={pending}>{pending ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />} Save inverter</Button><Notice message={message} error={message !== "Inverter saved."} /></form>;
}

function Field({ label, name, type = "text", defaultValue, ...props }: { label: string; name: string; type?: string; defaultValue: string | number; required?: boolean; min?: string; max?: string; step?: string }) { return <label className="field"><span>{label}</span><input name={name} type={type} defaultValue={defaultValue} title={String(defaultValue)} {...props} /></label>; }

export function InvertersRoute() {
  const state = useResource<State>("/api/state"); const [add, setAdd] = useState(false); const [selected, setSelected] = useState<number | null>(null); const [recommendation, setRecommendation] = useState<Record<string, unknown> | null>(null); const [aiMessage, setAiMessage] = useState<string | null>(null); const [aiPending, setAiPending] = useState(false);
  const selectedInverter = state.data?.inverters.find((inverter) => inverter.id === selected) ?? state.data?.inverters[0];
  const preview = async () => { if (!selectedInverter?.id) return; setAiPending(true); setAiMessage(null); try { const result = await apiForm("/api/ai/recommend", { inverter_id: selectedInverter.id }); setRecommendation((result.data?.recommendation as Record<string, unknown>) ?? null); setAiMessage("Recommendation preview loaded. Applying will re-check weather."); } catch (cause) { setAiMessage((cause as Error).message); } finally { setAiPending(false); } };
  if (state.loading && !state.data) return <LoadingState label="Loading inverter profiles" />; if (state.error && !state.data) return <ErrorState>Unable to load inverter profiles.</ErrorState>; const inverters = state.data!.inverters;
  return <Page title="Inverters" description="Persisted profile settings; saving a profile does not claim a hardware write." actions={<Button onClick={() => setAdd((value) => !value)}><Plus size={16} /> Add inverter</Button>}><div className="form-stack">{inverters.map((inverter) => <InverterForm key={inverter.id ?? inverter.name} inverter={inverter} onSaved={state.reload} />)}{add && <InverterForm onSaved={async () => { setAdd(false); await state.reload(); }} />}</div><section className="section ai-panel"><div className="section-heading"><div><h2>Weather recommendation</h2><p>Preview a policy recommendation for one profile, then re-check weather when applying it.</p></div><CloudSun aria-hidden="true" /></div>{inverters.length ? <><label className="field"><span>Inverter</span><select value={selectedInverter?.id ?? ""} onChange={(event) => setSelected(Number(event.target.value))}>{inverters.filter((inverter) => inverter.id).map((inverter) => <option value={inverter.id!} key={inverter.id}>{inverter.name} (ID {inverter.id})</option>)}</select></label><div className="inline-actions"><Button onClick={() => void preview()} disabled={aiPending}>{aiPending ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />} Preview recommendation</Button>{selectedInverter?.id && <MutationButton label="Re-check and apply" path="/api/ai/apply" fields={{ inverter_id: selectedInverter.id }} onSuccess={state.reload} primary />}</div><Notice message={aiMessage} error={aiMessage !== "Recommendation preview loaded. Applying will re-check weather."} />{recommendation && <dl className="recommendation">{Object.entries(recommendation).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{String(value)}</dd></div>)}</dl>}</> : <EmptyState title="No profile to recommend for">Create an inverter profile first.</EmptyState>}</section></Page>;
}

function time(value: number) { return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`; }
function minutes(value: string) { if (value === "24:00") return 1440; const [h, m] = value.split(":").map(Number); return h * 60 + m; }
function isValidTimeText(value: string) {
  const match = /^(\d{2}):(\d{2})$/.exec(value);
  if (!match) return false;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  return hour <= 23 && minute <= 59 || hour === 24 && minute === 0;
}
function pickerParts(value: number) {
  const bounded = Math.max(0, Math.min(value, 1440));
  const hour = Math.floor(bounded / 60);
  return { hour, minute: hour === 24 ? 0 : bounded % 60 === 30 ? 30 : 0 };
}
const pickerHours = Array.from({ length: 25 }, (_, hour) => String(hour).padStart(2, "0"));
const pickerMinutes = ["00", "30"] as const;
export function TouTierInputs({ period, update }: { period: Tou; update: (field: keyof Tou, value: number) => void }) {
  if (period.direction !== "export") return <td colSpan={3} aria-label={`No export tiers for ${period.label}`}>—</td>;
  return <><td><input aria-label={`Tier allowance for ${period.label}`} type="number" min="0" step="0.001" value={period.export_tier_kwh} onChange={(e) => update("export_tier_kwh", Number(e.target.value))} /></td><td><input aria-label={`Tier-one rate for ${period.label}`} type="number" min="0" step="0.01" value={period.export_tier_rate_cents_per_kwh} onChange={(e) => update("export_tier_rate_cents_per_kwh", Number(e.target.value))} /></td><td><input aria-label={`Excess rate for ${period.label}`} type="number" min="0" step="0.01" value={period.export_excess_rate_cents_per_kwh} onChange={(e) => update("export_excess_rate_cents_per_kwh", Number(e.target.value))} /></td></>;
}

export function TouTableColumnGroup() {
  return <colgroup><col className="tou-direction-column" /><col className="tou-label-column" /><col className="tou-time-column" /><col className="tou-time-column" /><col className="tou-rate-column" /><col className="tou-tier-allowance-column" /><col className="tou-rate-column" /><col className="tou-rate-column" /><col className="tou-actions-column" /></colgroup>;
}

function TouTimePicker({ period, field, update }: { period: Tou; field: "start_minute" | "end_minute"; update: (value: number) => void }) {
  const isStart = field === "start_minute";
  const fieldLabel = isStart ? "Start" : "End";
  const inputClass = isStart ? "tou-time-control tou-start-time-input" : "tou-time-control";
  const controlClass = isStart ? "tou-start-time-control" : "tou-end-time-control";
  const pickerLabel = `Choose ${fieldLabel.toLowerCase()} time for ${period.label}`;
  const dialogId = `${field}-time-picker-${period.id}`;
  const controlRef = useRef<HTMLSpanElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(() => time(period[field]));
  const [position, setPosition] = useState({ top: 0, left: 16 });
  const selectedValue = period[field];
  const { hour, minute } = pickerParts(selectedValue);
  useEffect(() => { setDraft(time(selectedValue)); }, [selectedValue]);
  useEffect(() => {
    if (!open) return;
    dialogRef.current?.focus();
    const updatePosition = () => {
      const rect = controlRef.current?.getBoundingClientRect();
      if (!rect) return;
      const width = 320;
      setPosition({ top: rect.bottom + 8, left: Math.max(16, Math.min(rect.right - width, window.innerWidth - width - 16)) });
    };
    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => { window.removeEventListener("resize", updatePosition); window.removeEventListener("scroll", updatePosition, true); };
  }, [open]);
  useEffect(() => { if (!open) controlRef.current?.querySelector<HTMLButtonElement>("button")?.focus(); }, [open]);
  const selectHour = (nextHour: string) => update(Number(nextHour) * 60 + (Number(nextHour) === 24 ? 0 : minute));
  const selectMinute = (nextMinute: string) => { if (hour === 24 && nextMinute === "30") return; update(hour * 60 + Number(nextMinute)); };
  return <span ref={controlRef} className={`tou-time-control ${controlClass}`}>
    <input className={inputClass} aria-label={`${fieldLabel} time for ${period.label}`} type="text" inputMode="numeric" pattern="(?:(?:[01]\d|2[0-3]):[0-5]\d|24:00)" placeholder="HH:MM" value={draft} onChange={(event) => { const value = event.target.value; setDraft(value); if (isValidTimeText(value)) update(minutes(value)); }} />
    <Button className="tou-time-picker-button" type="button" aria-label="Show time picker" title="Show time picker" aria-expanded={open} aria-controls={open ? dialogId : undefined} onClick={() => setOpen(true)}><Clock className="tou-time-picker-icon" aria-hidden="true" size={16} /></Button>
    {open && <div ref={dialogRef} id={dialogId} className="tou-time-picker-popover" role="dialog" aria-modal="true" aria-labelledby={`${dialogId}-title`} tabIndex={-1} style={{ top: position.top, left: position.left }} onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }}>
      <div className="tou-time-picker-heading"><strong id={`${dialogId}-title`}>{pickerLabel}</strong><Button type="button" className="tou-time-picker-close" onClick={() => setOpen(false)}>Close</Button></div>
      <div className="tou-time-picker-fields">
        <label><span>Hour</span><select aria-label="Hour" value={String(hour).padStart(2, "0")} onChange={(event) => selectHour(event.target.value)}>{pickerHours.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <label><span>Minute</span><select aria-label="Minute" value={String(minute).padStart(2, "0")} onChange={(event) => selectMinute(event.target.value)}>{pickerMinutes.map((value) => <option key={value} value={value} disabled={hour === 24 && value === "30"}>{value}</option>)}</select></label>
      </div>
    </div>}
  </span>;
}

export function StartTimeField({ period, update }: { period: Tou; update: (value: number) => void }) {
  return <TouTimePicker period={period} field="start_minute" update={update} />;
}

export function EndTimeField({ period, update }: { period: Tou; update: (value: number) => void }) {
  return <TouTimePicker period={period} field="end_minute" update={update} />;
}

function TouEditor({ plan, onSaved }: { plan: Plan; onSaved: () => Promise<void> }) {
  const resource = useResource<components["schemas"]["TouPeriodsResponse"]>(`/api/plans/${plan.id}/tou`); const [periods, setPeriods] = useState<Tou[]>([]); const [message, setMessage] = useState<string | null>(null); const [pending, setPending] = useState(false);
  useEffect(() => { if (resource.data?.plan_id === plan.id) setPeriods(resource.data.periods); }, [resource.data, plan.id]);
  const save = async () => { const payload = periods.map(({ direction, label, start_minute, end_minute, rate_cents_per_kwh, export_tier_kwh, export_tier_rate_cents_per_kwh, export_excess_rate_cents_per_kwh }) => ({ direction, label, start_minute: time(start_minute), end_minute: time(end_minute), rate_cents_per_kwh, export_tier_kwh: direction === "export" ? export_tier_kwh : 0, export_tier_rate_cents_per_kwh: direction === "export" ? export_tier_rate_cents_per_kwh : 0, export_excess_rate_cents_per_kwh: direction === "export" ? export_excess_rate_cents_per_kwh : 0 })); setPending(true); setMessage(null); try { await apiForm(`/api/tou/${plan.id}`, { payload: JSON.stringify(payload), daily_supply_charge: `$${(plan.daily_supply_charge_cents / 100).toFixed(2)}` }); await onSaved(); setMessage("TOU schedule saved."); } catch (cause) { setMessage((cause as Error).message); } finally { setPending(false); } };
  const update = (index: number, field: keyof Tou, value: string | number) => setPeriods((all) => all.map((period, i) => i === index ? { ...period, [field]: value } : period));
  const ready = resource.data?.plan_id === plan.id;
  if (resource.error && !ready) return <ErrorState>Unable to load the TOU schedule. <button className="link-button" onClick={() => void resource.reload()}>Try again</button></ErrorState>;
  if ((resource.loading && !ready) || !ready) return <LoadingState label="Loading TOU schedule" />;
  return <section className="section tou-editor"><div className="section-heading"><div><h2>TOU schedule</h2><p>Times must be 30-minute aligned. Export rows may use a daily tier; zero values keep the ordinary rate.</p></div></div><div className="table-wrap"><table className="tou-table"><TouTableColumnGroup /><thead><tr><th>Direction</th><th>Label</th><th>Start</th><th>End</th><th>Rate (c/kWh)</th><th>Tier allowance (kWh/day)</th><th>Tier-one (c/kWh)</th><th>Excess (c/kWh)</th><th>Actions</th></tr></thead><tbody>{periods.map((period, index) => <tr key={period.id}><td><select aria-label={`Direction for ${period.label}`} value={period.direction} onChange={(e) => update(index, "direction", e.target.value)}><option value="import">Import</option><option value="export">Export</option></select></td><td><input aria-label={`Label for TOU period ${index + 1}`} value={period.label} onChange={(e) => update(index, "label", e.target.value)} /></td><td><StartTimeField period={period} update={(value) => update(index, "start_minute", value)} /></td><td><EndTimeField period={period} update={(value) => update(index, "end_minute", value)} /></td><td className="tou-rate-cell"><input className="tou-rate-input" aria-label={`Rate for ${period.label} in cents per kilowatt hour`} type="number" min="0" step="0.01" value={period.rate_cents_per_kwh} onChange={(e) => update(index, "rate_cents_per_kwh", Number(e.target.value))} /></td><TouTierInputs period={period} update={(field, value) => update(index, field, value)} /><td><Button type="button" aria-label={`Remove ${period.label}`} onClick={() => { if (window.confirm(`Remove the ${period.label} period? It will be deleted when you save the complete schedule.`)) setPeriods((all) => all.filter((_, i) => i !== index)); }}><Trash2 aria-hidden="true" size={16} /></Button></td></tr>)}</tbody></table></div><div className="inline-actions"><Button onClick={() => setPeriods((all) => [...all, { id: Date.now(), plan_id: plan.id, direction: "import", label: "New period", start_minute: 0, end_minute: 30, rate_cents_per_kwh: 0, export_tier_kwh: 0, export_tier_rate_cents_per_kwh: 0, export_excess_rate_cents_per_kwh: 0 }])}><Plus size={16} /> Add period</Button><Button variant="primary" onClick={() => void save()} disabled={pending}>{pending ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />} Save TOU schedule</Button></div><Notice message={message} error={message !== "TOU schedule saved."} /></section>;
}

function PlanForm({ plan, onSaved, onDeleted }: { plan?: Plan; onSaved: () => Promise<void>; onDeleted?: () => Promise<void> }) {
  const [pending, setPending] = useState(false); const [message, setMessage] = useState<string | null>(null);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const f = new FormData(event.currentTarget); setPending(true); setMessage(null);
    try {
      await apiForm("/api/plans", { plan_id: plan?.id, provider_name: String(f.get("provider_name")), plan_name: String(f.get("plan_name")), billing_cycle: String(f.get("billing_cycle")), billing_start_day: Number(f.get("billing_start_day")), billing_start_month: Number(f.get("billing_start_month")), daily_supply_charge: String(f.get("daily_supply_charge")), notes: String(f.get("notes")) });
      await onSaved(); setMessage("Plan saved and selected as active.");
    } catch (cause) { setMessage((cause as Error).message); } finally { setPending(false); }
  };
  const remove = async () => {
    if (!plan?.id || !window.confirm(`Delete ${plan.provider_name} — ${plan.plan_name}? This cannot be undone.`)) return;
    setPending(true); setMessage(null);
    try { await apiDelete(`/api/plans/${plan.id}`); await onDeleted?.(); setMessage("Plan deleted."); }
    catch (cause) { setMessage((cause as Error).message); } finally { setPending(false); }
  };
  return <form className="form-card" aria-label={plan ? `${plan.plan_name} settings` : "Add plan"} onSubmit={(e) => void submit(e)}><div className="form-card-title"><h2>{plan ? `${plan.provider_name} — ${plan.plan_name}` : "Add plan"}</h2></div><div className="form-grid"><Field label="Provider" name="provider_name" defaultValue={plan?.provider_name ?? ""} required /><Field label="Plan name" name="plan_name" defaultValue={plan?.plan_name ?? ""} required /><label className="field"><span>Billing cycle</span><select name="billing_cycle" defaultValue={plan?.billing_cycle ?? "monthly"}><option value="monthly">Monthly</option><option value="quarterly">Quarterly</option></select></label><Field label="Billing start day" name="billing_start_day" type="number" min="1" max="31" defaultValue={plan?.billing_start_day ?? 1} required /><Field label="Billing start month" name="billing_start_month" type="number" min="1" max="12" defaultValue={plan?.billing_start_month ?? 1} required /><Field label="Daily supply charge" name="daily_supply_charge" defaultValue={`$${((plan?.daily_supply_charge_cents ?? 0) / 100).toFixed(2)}`} required /><label className="field full"><span>Notes</span><textarea name="notes" defaultValue={plan?.notes ?? ""} /></label></div><div className="inline-actions"><Button type="submit" variant="primary" disabled={pending}>{pending ? <LoaderCircle className="spin" size={16} /> : plan ? <Pencil aria-hidden="true" size={16} /> : <Save aria-hidden="true" size={16} />} Save plan</Button>{plan && <Button type="button" onClick={() => void remove()} disabled={pending}><Trash2 aria-hidden="true" size={16} /> Delete plan</Button>}</div><Notice message={message} error={message !== "Plan saved and selected as active." && message !== "Plan deleted."} /></form>;
}

export function PlansRoute() {
  const state = useResource<State>("/api/state"); const [add, setAdd] = useState(false); const [selected, setSelected] = useState<number | null>(null);
  if (state.loading && !state.data) return <LoadingState label="Loading plans" />; if (state.error && !state.data) return <ErrorState>Unable to load power plans.</ErrorState>;
  const plans = state.data!.power_plans; const plan = plans.find((item) => item.id === selected) ?? plans.find((item) => item.id === state.data!.settings.active_plan_id) ?? plans[0];
  return <Page title="Plans & TOU" description="Plan data is saved on the backend; TOU pricing and billing remain server-authoritative." actions={<Button onClick={() => setAdd((value) => !value)}><Plus aria-hidden="true" size={16} /> Add plan</Button>}>
    {plans.length > 0 && <div className="plans-tabs" role="tablist" aria-label="Saved plans">{plans.map((item) => <button type="button" role="tab" id={`plan-tab-${item.id}`} aria-selected={item.id === plan?.id} aria-controls={`plan-panel-${item.id}`} className="plans-tab" key={item.id} onClick={() => setSelected(item.id)}>{item.plan_name}</button>)}</div>}
    {plan ? <div className="form-stack" role="tabpanel" id={`plan-panel-${plan.id}`} aria-labelledby={`plan-tab-${plan.id}`}><PlanForm key={`plan-${plan.id}`} plan={plan} onSaved={state.reload} onDeleted={async () => { setSelected(null); await state.reload(); }} /><TouEditor key={`tou-${plan.id}`} plan={plan} onSaved={state.reload} />{add && <PlanForm onSaved={async () => { setAdd(false); await state.reload(); }} />}</div> : <>{add && <PlanForm onSaved={async () => { setAdd(false); await state.reload(); }} />}<EmptyState title="No power plans">Add a plan before creating a time-of-use schedule.</EmptyState></>}
  </Page>;
}

function CloseDayButton({ onClosed }: { onClosed: () => Promise<void> }) { const [pending, setPending] = useState(false); const [message, setMessage] = useState<string | null>(null); const close = async () => { if (!window.confirm("Close and finalize the current local day?")) return; setPending(true); setMessage(null); try { const result = await apiPost<components["schemas"]["CloseDayResponse"]>("/api/close-day"); await onClosed(); setMessage(`Day ${result.day} finalized.`); } catch (cause) { setMessage((cause as Error).message); } finally { setPending(false); } }; return <span className="mutation"><Button type="button" onClick={() => void close()} disabled={pending}>{pending ? "Closing…" : "Close day"}</Button><Notice message={message} error={message !== null && !message.startsWith("Day ")} /></span>; }

function DailyTouEnergy({ value }: { value: number | null | undefined }) { return value == null ? <td aria-label="Not available">—</td> : <td>{formatEnergy(value)}</td>; }
function DailySiteTotalsTable({ rows }: { rows: components["schemas"]["DailySiteTotal"][] }) { if (!rows.length) return <EmptyState title="No daily site totals yet">Poll the inverters to record authoritative local-day meter totals.</EmptyState>; return <div className="table-wrap" tabIndex={0} aria-label="Daily site totals and billing"><table><thead><tr><th>Date</th><th>Solar Gen</th><th>Load Use</th><th><span>Grid Import</span><br /><span>Off-peak</span></th><th><span>Grid Import</span><br /><span>Peak</span></th><th><span>Grid Export</span><br /><span>Off-peak</span></th><th><span>Grid Export</span><br /><span>Peak</span></th><th>Batt Charge kWh</th><th>Batt Discharge kWh</th><th><span>Minimum</span><br /><span>SOC</span></th><th><span>Maximum</span><br /><span>SOC</span></th><th>Power Plan</th><th>Daily Amount</th></tr></thead><tbody>{rows.map((row) => <tr key={row.day}><td>{formatAuDate(row.day)}</td><td>{formatEnergy(row.solar_kwh)}</td><td>{formatEnergy(row.load_kwh)}</td><DailyTouEnergy value={row.grid_import_off_peak_kwh} /><DailyTouEnergy value={row.grid_import_peak_kwh} /><DailyTouEnergy value={row.grid_export_off_peak_kwh} /><DailyTouEnergy value={row.grid_export_peak_kwh} /><td>{formatEnergy(row.battery_charge_kwh)}</td><td>{formatEnergy(row.battery_discharge_kwh)}</td><DailySocValue value={row.battery_level_min_percent} /><DailySocValue value={row.battery_level_max_percent} source={row.battery_level_max_percent_source} /><td>{row.plan_name ?? "—"}</td><td>{formatMoney(row.daily_bill_amount_cents)}</td></tr>)}</tbody></table></div>; }
function DailySocValue({ value, source }: { value: number | null | undefined; source?: components["schemas"]["DailySiteTotal"]["battery_level_max_percent_source"] }) { if (value == null) return <td aria-label="Not available">—</td>; const derived = source === "derived_from_grid_export_off_peak"; return <td title={derived ? "Derived from positive Grid Export Off-peak; not directly observed." : undefined}>{formatBatteryLevel(value)}{derived && <span className="sr-only"> (derived from positive Grid Export Off-peak)</span>}</td>; }

export function BillingRoute() { const bill = useResource<Bill>("/api/bill"); const [direction, setDirection] = useState("all"); if (bill.loading && !bill.data) return <LoadingState label="Loading billing audit" />; if (bill.error && !bill.data) return <ErrorState>Unable to load billing details. <button className="link-button" onClick={() => void bill.reload()}>Try again</button></ErrorState>; const data = bill.data!; const filtered = { ...data, rows: (data.rows ?? []).filter((row) => direction === "all" || row.direction === direction) }; return <Page title="Billing" description="Backend-calculated current bill for the active billing cycle and meter-authoritative daily site totals." actions={<><Button onClick={() => void bill.reload()}><RefreshCw size={16} /> Refresh</Button><CloseDayButton onClosed={bill.reload} /></>}><div className="billing-summary"><Metric label="Active plan" value={data.plan?.plan_name ?? "None selected"} /><Metric label="Current bill" value={formatMoney(data.total_cents)} /><Metric label="Imported today" value={formatEnergy(data.today_grid_import_kwh)} /><Metric label="Exported today" value={formatEnergy(data.today_grid_export_kwh)} /></div>{!data.plan && <section className="alert-banner"><CircleAlert size={18} />No active plan: no pricing can be calculated until a plan is selected in Settings.</section>}<section className="section"><div className="section-heading"><div><h2>Daily site totals and billing</h2><p>Local-day totals from persisted daily counters; bill amounts from the same day’s server-calculated bill rows.</p></div></div><DailySiteTotalsTable rows={data.daily_site_totals ?? []} /></section><section className="section"><div className="section-heading"><div><h2>Bill audit</h2><p>{data.plan ? `${data.plan.provider_name} — ${data.plan.plan_name}` : "No active plan"}</p><label className="filter"><span>Direction</span><select value={direction} onChange={(e) => setDirection(e.target.value)}><option value="all">All</option><option value="import">Import</option><option value="export">Export</option><option value="fixed">Fixed</option></select></label></div></div><BillTable bill={filtered} /></section></Page>; }

export function SettingsRoute() { const state = useResource<State>("/api/state"); const [pending, setPending] = useState(false); const [message, setMessage] = useState<string | null>(null); if (state.loading && !state.data) return <LoadingState label="Loading settings" />; if (state.error && !state.data) return <ErrorState>Unable to load settings.</ErrorState>; const data = state.data!; const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const f = new FormData(event.currentTarget); const theme = String(f.get("theme")); document.documentElement.dataset.theme = theme; setPending(true); try { await apiForm("/api/settings", { theme, mode: String(f.get("mode")), site_name: String(f.get("site_name")), site_lat: Number(f.get("site_lat")), site_lon: Number(f.get("site_lon")), site_timezone: String(f.get("site_timezone")), poll_interval_seconds: Number(f.get("poll_interval_seconds")), active_plan_id: String(f.get("active_plan_id")) }); await state.reload(); setMessage("Settings saved."); } catch (cause) { setMessage((cause as Error).message); } finally { setPending(false); } }; return <Page title="Settings" description="Site identity, refresh cadence, active tariff plan and interface theme."><form className="form-card settings-form" onSubmit={(e) => void submit(e)}><div className="form-grid"><label className="field"><span>Theme</span><select name="theme" defaultValue={data.settings.theme}><option value="classic-light">Classic light</option><option value="classic-dark">Classic dark</option><option value="deep-ocean">Deep ocean</option><option value="ember-core">Ember core</option></select></label><label className="field"><span>Mode</span><select name="mode" defaultValue={data.settings.mode}><option value="manual">Manual</option><option value="ai">AI</option></select></label><Field label="Site name" name="site_name" defaultValue={data.settings.site_name} required /><Field label="Latitude" name="site_lat" type="number" step="0.000001" defaultValue={data.settings.site_lat} required /><Field label="Longitude" name="site_lon" type="number" step="0.000001" defaultValue={data.settings.site_lon} required /><Field label="IANA timezone" name="site_timezone" defaultValue={data.settings.site_timezone} required /><Field label="Poll interval (seconds)" name="poll_interval_seconds" type="number" min="5" max="3600" defaultValue={data.settings.poll_interval_seconds} required /><label className="field"><span>Active plan</span><select title={data.power_plans.find((plan) => plan.id === data.settings.active_plan_id) ? `${data.power_plans.find((plan) => plan.id === data.settings.active_plan_id)?.provider_name} — ${data.power_plans.find((plan) => plan.id === data.settings.active_plan_id)?.plan_name}` : "None"} name="active_plan_id" defaultValue={data.settings.active_plan_id ?? ""}><option value="">None</option>{data.power_plans.map((plan) => <option value={plan.id} key={plan.id}>{plan.provider_name} — {plan.plan_name}</option>)}</select></label></div><Button type="submit" variant="primary" disabled={pending}>{pending ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />} Save settings</Button><Notice message={message} error={message !== "Settings saved."} /></form></Page>; }
