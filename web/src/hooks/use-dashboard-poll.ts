import { useCallback, useEffect, useRef, useState } from "react";
import { dashboardSnapshot, type DashboardSnapshot } from "../api/adapters";
import { apiGet } from "../api/client";

type PollState = { data: DashboardSnapshot | null; error: Error | null; checking: boolean; checkedAt: Date | null };

export function useDashboardPoll() {
  const [state, setState] = useState<PollState>({ data: null, error: null, checking: true, checkedAt: null });
  const requestId = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const retryTimer = useRef<number | null>(null);
  const retryCount = useRef(0);
  const loadRef = useRef<() => Promise<void>>(() => Promise.resolve());
  const load = useCallback(async () => {
    controller.current?.abort();
    controller.current = new AbortController();
    const current = ++requestId.current;
    setState((old) => ({ ...old, checking: true }));
    try {
      const result = dashboardSnapshot(await apiGet<unknown>("/api/state", controller.current.signal));
      retryCount.current = 0;
      if (current === requestId.current) setState({ data: result, error: null, checking: false, checkedAt: new Date() });
    } catch (error) {
      if ((error as DOMException).name === "AbortError") return;
      if (current === requestId.current) setState((old) => ({ ...old, error: error as Error, checking: false }));
      if (document.visibilityState === "visible") {
        const delay = Math.min(30_000, 1_000 * 2 ** retryCount.current++);
        retryTimer.current = window.setTimeout(() => void loadRef.current(), delay);
      }
    }
  }, []);
  loadRef.current = load;
  useEffect(() => {
    void load();
    const visible = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", visible);
    const interval = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, Math.max((state.data?.settings.poll_interval_seconds ?? 10), 10) * 1000);
    return () => { document.removeEventListener("visibilitychange", visible); window.clearInterval(interval); if (retryTimer.current !== null) window.clearTimeout(retryTimer.current); controller.current?.abort(); requestId.current += 1; };
  }, [load, state.data?.settings.poll_interval_seconds]);
  return { ...state, refresh: load };
}
