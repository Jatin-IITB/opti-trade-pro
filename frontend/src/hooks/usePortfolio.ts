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
  // Null when the input is genuinely unavailable rather than zero: equity and
  // margin need the broker funds call, Greeks need a live spot. Render these
  // as "—", never as 0 — a zero delta reads as a flat book.
  equity: number | null;
  margin_used: number | null;
  margin_available: number | null;
  margin_utilization: number | null;
  spot: number | null;
  aggregate_greeks: PortfolioGreeks | null;
  greeks_priced: number;
  synced: boolean;
}

export interface Holding {
  instrument_key: string;
  trading_symbol: string;
  exchange: string;
  quantity: number;
  average_price: number;
  last_price: number;
  pnl: number;
  day_change: number;
  day_change_percentage: number;
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
  spot: number | null;
  /**
   * Upstox tokens expire daily and standard apps get no refresh token, so
   * this eventually goes true for any session left running. Only a re-login
   * clears it — surface a prompt rather than showing a stale book as current.
   */
  auth_required: boolean;
  last_error: string | null;
}

interface PortfolioState {
  summary: PortfolioSummary | null;
  signals: PortfolioSignal[];
  holdings: Holding[];
  syncStatus: SyncStatus | null;
  loading: boolean;
  error: string | null;
}

const POLL_INTERVAL_MS = 60_000;
const WS_RECONNECT_MS = 3_000;

async function fetchJson<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json() as Promise<T>;
}

export function usePortfolio(): PortfolioState & { refresh: () => void } {
  const [state, setState] = useState<PortfolioState>({
    summary: null,
    signals: [],
    holdings: [],
    syncStatus: null,
    loading: true,
    error: null,
  });
  const wsRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchAll = useCallback(async () => {
    // Settled, not all: one transient 500 on /signals must not blank the
    // summary, holdings and sync status too. Each slice keeps its last-good
    // value and only the failing one degrades.
    const [summary, signals, holdings, syncStatus] = await Promise.allSettled([
      fetchJson<PortfolioSummary>("/api/v1/portfolio/summary"),
      fetchJson<PortfolioSignal[]>("/api/v1/portfolio/signals"),
      fetchJson<Holding[]>("/api/v1/portfolio/holdings"),
      fetchJson<SyncStatus>("/api/v1/portfolio/sync/status"),
    ]);

    const failures = [summary, signals, holdings, syncStatus]
      .filter((r): r is PromiseRejectedResult => r.status === "rejected")
      .map((r) =>
        r.reason instanceof Error ? r.reason.message : String(r.reason),
      );

    setState((prev) => ({
      summary: summary.status === "fulfilled" ? summary.value : prev.summary,
      signals: signals.status === "fulfilled" ? signals.value : prev.signals,
      holdings: holdings.status === "fulfilled" ? holdings.value : prev.holdings,
      syncStatus:
        syncStatus.status === "fulfilled" ? syncStatus.value : prev.syncStatus,
      loading: false,
      // Surface the auth/summary failure preferentially: it is the one that
      // tells the user to log in.
      error: failures.length === 0 ? null : failures[0],
    }));
  }, []);

  useEffect(() => {
    let closed = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    fetchAll();
    timerRef.current = setInterval(fetchAll, POLL_INTERVAL_MS);

    const connect = () => {
      if (closed) return;
      // The route is /dashboard/ws/{client_id}; omitting the id yields a 404
      // and the socket silently never opens, degrading the page to poll-only.
      const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      // randomUUID, not Math.random().toString(36): the latter can produce a
      // 1-2 char suffix, and WebSocketManager keys clients by id — a
      // collision evicts another client's socket.
      const clientId = `portfolio-${crypto.randomUUID()}`;
      const ws = new WebSocket(
        `${wsProtocol}//${window.location.host}/api/v1/dashboard/ws/${clientId}`,
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

      // The dev server restarts on every backend edit, and a dropped socket
      // was previously never re-established — the page degraded to 60s
      // polling with no indication.
      ws.onclose = () => {
        if (closed) return;
        reconnectTimer = setTimeout(connect, WS_RECONNECT_MS);
      };
      ws.onerror = () => ws.close();
    };

    connect();

    return () => {
      closed = true;
      if (timerRef.current) clearInterval(timerRef.current);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      wsRef.current?.close();
    };
  }, [fetchAll]);

  return { ...state, refresh: fetchAll };
}
