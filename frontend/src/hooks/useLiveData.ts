import { useEffect, useRef, useState, useCallback } from "react";
import demoData from "../data/demo.json";

export type DataMode = "demo" | "live" | "connecting";

export interface DashboardData {
  volSurface: typeof demoData.volSurface;
  greeksComparison: typeof demoData.greeksComparison;
  scenarioGrid: typeof demoData.scenarioGrid;
  pnlExplain: typeof demoData.pnlExplain;
  higherOrderGreeks: typeof demoData.higherOrderGreeks;
  optionChain: typeof demoData.optionChain;
  essviCalibration: typeof demoData.essviCalibration;
  backtestEquity: typeof demoData.backtestEquity;
  vrpSignal: typeof demoData.vrpSignal;
  riskDashboard: typeof demoData.riskDashboard;
}

export interface LiveDataState {
  mode: DataMode;
  data: DashboardData;
  lastUpdate: number | null;
  spot: number | null;
  underlying: string;
  error: string | null;
}

const INITIAL_STATE: LiveDataState = {
  mode: "connecting",
  data: demoData as DashboardData,
  lastUpdate: null,
  spot: null,
  underlying: "NIFTY",
  error: null,
};

const MAX_BACKOFF_MS = 30_000;
const BASE_BACKOFF_MS = 1_000;

function backoffMs(attempt: number): number {
  return Math.min(BASE_BACKOFF_MS * 2 ** attempt, MAX_BACKOFF_MS);
}

export function useLiveData(): LiveDataState {
  const [state, setState] = useState<LiveDataState>(INITIAL_STATE);
  const wsRef = useRef<WebSocket | null>(null);
  const attemptRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const mergePayload = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (payload: Record<string, any>) => {
      setState((prev) => ({
        ...prev,
        mode: "live",
        error: null,
        lastUpdate: Date.now(),
        spot: payload.spot ?? prev.spot,
        underlying: payload.underlying ?? prev.underlying,
        data: {
          ...prev.data,
          ...(payload.volSurface != null && {
            volSurface: payload.volSurface,
          }),
          ...(payload.optionChain != null && {
            optionChain: payload.optionChain,
          }),
          ...(payload.greeksComparison != null && {
            greeksComparison: payload.greeksComparison,
          }),
          ...(payload.essviCalibration != null && {
            essviCalibration: payload.essviCalibration,
          }),
          ...(payload.riskDashboard != null && {
            riskDashboard: payload.riskDashboard,
          }),
        },
      }));
    },
    [],
  );

  const connect = useCallback(() => {
    const clientId = crypto.randomUUID();
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/api/v1/dashboard/ws/${clientId}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      attemptRef.current = 0;
      setState((prev) => ({ ...prev, mode: "connecting", error: null }));
      ws.send(JSON.stringify({ type: "subscribe", symbols: ["NIFTY"] }));
      ws.send(JSON.stringify({ type: "request_snapshot" }));
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "dashboard_update" && msg.data) {
          mergePayload(msg.data);
        }
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      wsRef.current = null;
      const delay = backoffMs(attemptRef.current);
      attemptRef.current += 1;
      setState((prev) => ({
        ...prev,
        mode: prev.mode === "live" ? "live" : "demo",
        error:
          prev.mode === "live" ? "Connection lost — reconnecting…" : null,
      }));
      timerRef.current = setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [mergePayload]);

  useEffect(() => {
    connect();
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, [connect]);

  return state;
}
