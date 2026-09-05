import type { ReactNode } from "react";

export function LoadingState({ label = "Loading data" }: { label?: string }) { return <section className="state-panel" aria-busy="true" aria-live="polite"><h2>{label}</h2><p>Waiting for the server response.</p></section>; }
export function ErrorState({ title = "Data unavailable", children }: { title?: string; children: ReactNode }) { return <section className="state-panel" role="alert"><h2>{title}</h2><p>{children}</p></section>; }
export function EmptyState({ title, children }: { title: string; children: ReactNode }) { return <section className="state-panel"><h2>{title}</h2><p>{children}</p></section>; }
