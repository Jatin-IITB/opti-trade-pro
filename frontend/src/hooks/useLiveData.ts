import { useCallback, useEffect, useRef, useState } from "react";
import demoData from "../data/demo.json";

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
}

type ConnectionStatus = "disconnected" | "connecting" | "connected" | "error";

interface LiveState {
  data: DashboardData;
  status: ConnectionStatus;
  spot: number;
  underlying: string;
  lastUpdate: number | null;
  isLive: boolean;
}

function generateClientId(): string {
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function useLiveData(): LiveState & {
  subscribe: (symbol: string) => void;
  requestSnapshot: () => void;
} {
  const [state, setState] = useState<LiveState>({
    data: demoData as DashboardData,
    status: "disconnected",
    spot: demoData.volSurface.spot,
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
              // Only keys listed here can ever replace the bundled demo
              // baseline. A key the backend computes but that is missing from
              // this list renders demo data forever, silently.
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
              // a reason until enough days are captured, and that state must
              // replace the demo baseline too — otherwise a fresh install
              // shows a fabricated equity curve instead of "still collecting".
              ...(d.vrpSignal && { vrpSignal: d.vrpSignal }),
              ...(d.backtestEquity && { backtestEquity: d.backtestEquity }),
              ...(d.pnlExplain && { pnlExplain: d.pnlExplain }),
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
