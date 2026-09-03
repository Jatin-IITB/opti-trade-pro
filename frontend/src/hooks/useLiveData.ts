import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Every field is nullable and starts null.
 *
 * This used to be seeded from `demo.json`, a bundled chain priced off a
 * 20,000 NIFTY. Because the backend sends nothing until the first capture
 * completes, that baseline was not a brief flash before real data — it was
 * the steady state for as long as the market was shut, rendered
 * indistinguishably from live values. The file has been deleted rather than
 * left unreferenced, so there is nothing to fall back to again.
 *
 * Consumers must therefore treat null as "not yet known" and render that,
 * which `LiveGate` and `HistoryGate` do.
 */
export interface DashboardData {
  volSurface: any;
  greeksComparison: any;
  scenarioGrid: any;
  pnlExplain: any;
  higherOrderGreeks: any;
  optionChain: any;
  essviCalibration: any;
  backtestEquity: any;
  vrpSignal: any;
  riskDashboard: any;
  desk: any;
}

type ConnectionStatus = "disconnected" | "connecting" | "connected" | "error";

interface LiveState {
  data: DashboardData;
  status: ConnectionStatus;
  /** Null until the first capture; callers must not print it as a number. */
  spot: number | null;
  underlying: string;
  lastUpdate: number | null;
  isLive: boolean;
}

const NO_DATA: DashboardData = {
  volSurface: null,
  greeksComparison: null,
  scenarioGrid: null,
  pnlExplain: null,
  higherOrderGreeks: null,
  optionChain: null,
  essviCalibration: null,
  backtestEquity: null,
  vrpSignal: null,
  riskDashboard: null,
  desk: null,
};

function generateClientId(): string {
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function useLiveData(): LiveState & {
  subscribe: (symbol: string) => void;
  requestSnapshot: () => void;
} {
  const [state, setState] = useState<LiveState>({
    data: NO_DATA,
    status: "disconnected",
    spot: null,
    underlying: "NIFTY",
    lastUpdate: null,
    isLive: false,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clientId = useRef(generateClientId());

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    setState((prev) => ({ ...prev, status: "connecting" }));

    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(
      `${wsProtocol}//${window.location.host}/api/v1/dashboard/ws/${clientId.current}`,
    );
    wsRef.current = ws;

    ws.onopen = () => {
      setState((prev) => ({ ...prev, status: "connected" }));
      ws.send(JSON.stringify({ type: "request_snapshot" }));
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);

        if (msg.type === "dashboard_update" && msg.data) {
          const d = msg.data;
          setState((prev) => ({
            ...prev,
            data: {
              ...prev.data,
              // Only keys listed here are ever adopted from a broadcast. A
              // key the backend computes but that is missing from this list
              // stays null forever, so its panel reports "waiting for the
              // first captured chain" even while the data is arriving.
              ...(d.volSurface && { volSurface: d.volSurface }),
              ...(d.greeksComparison && { greeksComparison: d.greeksComparison }),
              ...(d.optionChain && { optionChain: d.optionChain }),
              ...(d.essviCalibration && {
                essviCalibration: d.essviCalibration,
              }),
              ...(d.riskDashboard && { riskDashboard: d.riskDashboard }),
              ...(d.scenarioGrid && { scenarioGrid: d.scenarioGrid }),
              ...(d.higherOrderGreeks && {
                higherOrderGreeks: d.higherOrderGreeks,
              }),
              // History-backed panels. These arrive with hasHistory:false and
              // a reason until enough days are captured; that state is real
              // information and must be adopted, so HistoryGate can report
              // how many days are still missing rather than how many are.
              ...(d.vrpSignal && { vrpSignal: d.vrpSignal }),
              ...(d.backtestEquity && { backtestEquity: d.backtestEquity }),
              ...(d.pnlExplain && { pnlExplain: d.pnlExplain }),
              // The paper desk. Carries its own kill-switch state, so a
              // dropped update must not leave a stale "clear" badge next to
              // a desk that has since halted — the backend always sends this
              // key, and on failure it reports the switch as engaged.
              ...(d.desk && { desk: d.desk }),
            },
            spot: d.spot ?? prev.spot,
            underlying: d.underlying ?? prev.underlying,
            lastUpdate: Date.now(),
            isLive: true,
          }));
        }
      } catch {
        /* ignore non-JSON frames */
      }
    };

    ws.onclose = () => {
      setState((prev) => ({
        ...prev,
        status: "disconnected",
        isLive: false,
      }));
      reconnectTimer.current = setTimeout(connect, 5000);
    };

    ws.onerror = () => {
      setState((prev) => ({ ...prev, status: "error" }));
    };
  }, []);

  const subscribe = useCallback((symbol: string) => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "subscribe", symbols: [symbol] }));
    }
  }, []);

  const requestSnapshot = useCallback(() => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "request_snapshot" }));
    }
  }, []);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { ...state, subscribe, requestSnapshot };
}
