const moneyFormatter = new Intl.NumberFormat("en-AU", { style: "currency", currency: "AUD", minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Convert API accounting signs to the user-facing billing signs. */
export function toDisplayMoneyCents(cents: number | null): number | null { return cents === null ? null : cents === 0 ? 0 : -cents; }
export function formatMoney(cents: number | null): string {
  const displayCents = toDisplayMoneyCents(cents);
  return displayCents === null ? "Unpriced" : moneyFormatter.format(displayCents / 100);
}
export function formatEnergy(kwh: number | null): string { return kwh === null ? "—" : `${new Intl.NumberFormat("en-AU", { maximumFractionDigits: 2 }).format(kwh)} kWh`; }
export function formatPower(kw: number | null): string { return kw === null ? "—" : `${new Intl.NumberFormat("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(kw)} kW`; }
export function formatAuDate(value: string): string { return new Intl.DateTimeFormat("en-AU", { day: "2-digit", month: "2-digit", year: "numeric" }).format(new Date(`${value}T00:00:00`)); }
