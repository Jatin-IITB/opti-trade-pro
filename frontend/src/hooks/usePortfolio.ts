import { useCallback, useEffect, useRef, useState } from "react";

interface PortfolioGreeks {
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
  rho: number;
  vanna: number;
  volga: number;
}

export interface PortfolioSummary {
  total_positions: number;
  core_positions: number;
  total_pnl: number;
  equity: number;
  aggregate_greeks: PortfolioGreeks;
  synced: boolean;
}

export interface PortfolioSignal {
  trading_symbol: string;
  option_type: string | null;
  strike_price: number | null;
  expiry: string | null;
  quantity: number;
  entry_price: number;
  current_price: number;
  pnl: number;
  pnl_pct: number;
  moneyness: string;
  days_to_expiry: number | null;
}

export interface SyncStatus {
  running: boolean;
  last_sync_ts: number | null;
  n_syncs: number;
  n_failures: number;
  position_count: number;
}

interface PortfolioState {
  summary: PortfolioSummary | null;
  signals: PortfolioSignal[];
  syncStatus: SyncStatus | null;
  loading: boolean;
  error: string | null;
}

const POLL_INTERVAL_MS = 60_000;

async function fetchJson<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json() as Promise<T>;
}

export function usePortfolio(): PortfolioState & { refresh: () => void } {
  const [state, setState] = useState<PortfolioState>({
    summary: null,
    signals: [],
    syncStatus: null,
    loading: true,
    error: null,
  });
  const wsRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [summary, signals, syncStatus] = await Promise.all([
        fetchJson<PortfolioSummary>("/api/v1/portfolio/summary"),
        fetchJson<PortfolioSignal[]>("/api/v1/portfolio/signals"),
        fetchJson<SyncStatus>("/api/v1/portfolio/sync/status"),
      ]);
      setState({ summary, signals, syncStatus, loading: false, error: null });
    } catch (e) {
      setState((prev) => ({
        ...prev,
        loading: false,
        error: e instanceof Error ? e.message : "Failed to fetch portfolio",
      }));
    }
  }, []);

  useEffect(() => {
    fetchAll();
    timerRef.current = setInterval(fetchAll, POLL_INTERVAL_MS);

    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(
      `${wsProtocol}//${window.location.host}/api/v1/dashboard/ws`,
    );
    wsRef.current = ws;

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "portfolio_update") {
          fetchAll();
        }
      } catch {
        /* ignore non-JSON frames */
      }
    };

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      ws.close();
    };
  }, [fetchAll]);

  return { ...state, refresh: fetchAll };
}
