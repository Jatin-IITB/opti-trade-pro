import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle,
  AlertCircle,
  ExternalLink,
  RefreshCw,
  LogOut,
  Plug,
} from "lucide-react";
import { useAuthStatus, type AuthStatus } from "../hooks/useAuthStatus";

interface BrokerConnector {
  id: string;
  name: string;
  description: string;
  status: "connected" | "disconnected" | "coming_soon";
  logoText: string;
  logoGradient: string;
  features: string[];
  authUrl?: string;
}

interface SyncStatus {
  running: boolean;
  last_sync_ts: number | null;
  n_syncs: number;
  n_failures: number;
  position_count: number;
}

function formatTime(ts: string | null): string {
  if (!ts) return "—";
  return new Date(ts).toLocaleString("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function formatSyncTime(ts: number | null): string {
  if (!ts) return "Never";
  return new Date(ts * 1000).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function StatusBadge({ status }: { status: BrokerConnector["status"] }) {
  if (status === "connected") {
    return (
      <span className="flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full bg-emerald-900/30 text-emerald-400 border border-emerald-700/50">
        <CheckCircle size={12} />
        Connected
      </span>
    );
  }
  if (status === "coming_soon") {
    return (
      <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-slate-800 text-slate-500 border border-slate-700">
        Coming Soon
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full bg-slate-800 text-slate-400 border border-slate-700">
      <AlertCircle size={12} />
      Not Connected
    </span>
  );
}

function ConnectorCard({
  connector,
  auth,
  syncStatus,
  onConnect,
  onDisconnect,
  onRefreshAuth,
}: {
  connector: BrokerConnector;
  auth: AuthStatus | null;
  syncStatus: SyncStatus | null;
  onConnect: () => void;
  onDisconnect: () => void;
  onRefreshAuth: () => void;
}) {
  const isConnected = connector.status === "connected";
  const isComingSoon = connector.status === "coming_soon";

  return (
    <div
      className={`bg-slate-800/50 rounded-xl border p-5 transition-colors ${
        isConnected
          ? "border-emerald-700/50"
          : isComingSoon
            ? "border-slate-700/50 opacity-60"
            : "border-slate-700 hover:border-slate-600"
      }`}
    >
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <div
            className={`w-10 h-10 rounded-lg bg-gradient-to-br ${connector.logoGradient} flex items-center justify-center text-white font-bold text-sm`}
          >
            {connector.logoText}
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-100">
              {connector.name}
            </h3>
            <p className="text-xs text-slate-500">{connector.description}</p>
          </div>
        </div>
        <StatusBadge status={connector.status} />
      </div>

      <div className="flex flex-wrap gap-1.5 mb-4">
        {connector.features.map((f) => (
          <span
            key={f}
            className="text-[10px] font-medium px-2 py-0.5 rounded bg-slate-700/50 text-slate-400"
          >
            {f}
          </span>
        ))}
      </div>

      {isConnected && auth && (
        <div className="bg-slate-900/50 rounded-lg p-3 mb-4 space-y-1.5">
          <div className="flex justify-between text-xs">
            <span className="text-slate-500">User</span>
            <span className="text-slate-300 font-mono">
              {auth.user_id ?? "—"}
            </span>
          </div>
          <div className="flex justify-between text-xs">
            <span className="text-slate-500">Token Expires</span>
            <span
              className={`font-mono ${auth.needs_refresh ? "text-amber-400" : "text-slate-300"}`}
            >
              {formatTime(auth.token_expires_at)}
              {auth.needs_refresh && " (refresh needed)"}
            </span>
          </div>
          {syncStatus && (
            <>
              <div className="flex justify-between text-xs">
                <span className="text-slate-500">Portfolio Sync</span>
                <span
                  className={`font-mono ${syncStatus.running ? "text-emerald-400" : "text-slate-400"}`}
                >
                  {syncStatus.running ? "Active" : "Paused"}
                </span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-slate-500">Last Sync</span>
                <span className="text-slate-300 font-mono">
                  {formatSyncTime(syncStatus.last_sync_ts)}
                </span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-slate-500">Positions</span>
                <span className="text-slate-300 font-mono">
                  {syncStatus.position_count}
                </span>
              </div>
              {syncStatus.n_failures > 0 && (
                <div className="flex justify-between text-xs">
                  <span className="text-slate-500">Failures</span>
                  <span className="text-red-400 font-mono">
                    {syncStatus.n_failures}
                  </span>
                </div>
              )}
            </>
          )}
        </div>
      )}

      <div className="flex gap-2">
        {isConnected ? (
          <>
            <button
              onClick={onRefreshAuth}
              className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-slate-700 text-slate-300 text-xs font-medium hover:bg-slate-600 transition-colors"
            >
              <RefreshCw size={12} />
              Refresh Token
            </button>
            <button
              onClick={onDisconnect}
              className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-red-900/20 text-red-400 text-xs font-medium hover:bg-red-900/40 transition-colors border border-red-800/30"
            >
              <LogOut size={12} />
              Disconnect
            </button>
          </>
        ) : isComingSoon ? (
          <button
            disabled
            className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-slate-800 text-slate-600 text-xs font-medium cursor-not-allowed"
          >
            <Plug size={12} />
            Coming Soon
          </button>
        ) : (
          <button
            onClick={onConnect}
            className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-blue-600 text-white text-xs font-medium hover:bg-blue-500 transition-colors"
          >
            <ExternalLink size={12} />
            Connect with {connector.name}
          </button>
        )}
      </div>
    </div>
  );
}

const BROKERS: BrokerConnector[] = [
  {
    id: "upstox",
    name: "Upstox",
    description: "F&O + equity broker — live positions, orders, holdings",
    status: "disconnected",
    logoText: "UP",
    logoGradient: "from-purple-500 to-violet-600",
    features: [
      "F&O Positions",
      "Equity Holdings",
      "Order Book",
      "P&L History",
      "Live Market Data",
      "Portfolio Sync",
    ],
    authUrl: "/api/v1/auth/login",
  },
  {
    id: "zerodha",
    name: "Zerodha",
    description: "Kite Connect API — positions, orders, market data",
    status: "coming_soon",
    logoText: "ZE",
    logoGradient: "from-orange-500 to-red-500",
    features: [
      "F&O Positions",
      "Equity Holdings",
      "GTT Orders",
      "Margins",
      "Live Market Data",
    ],
  },
  {
    id: "groww",
    name: "Groww",
    description: "Equity + mutual fund broker — holdings, orders",
    status: "coming_soon",
    logoText: "GR",
    logoGradient: "from-green-500 to-emerald-600",
    features: [
      "Equity Holdings",
      "Mutual Funds",
      "Order Book",
      "SIP Tracking",
    ],
  },
  {
    id: "miraeasset",
    name: "Mira Asset",
    description: "Full-service broker — F&O, equity, research",
    status: "coming_soon",
    logoText: "MA",
    logoGradient: "from-blue-500 to-cyan-500",
    features: ["F&O Positions", "Equity Holdings", "Research Reports"],
  },
];

export function ConnectorsPanel() {
  const { status: authStatus, loading: authLoading, refresh: refreshAuth } = useAuthStatus();
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);

  const isUpstoxConnected = authStatus?.authenticated === true;

  const fetchSyncStatus = useCallback(async () => {
    try {
      const resp = await fetch("/api/v1/portfolio/sync/status");
      if (resp.ok) {
        setSyncStatus(await resp.json());
      }
    } catch {
      /* sync service may not be running */
    }
  }, []);

  useEffect(() => {
    if (isUpstoxConnected) {
      fetchSyncStatus();
      const timer = setInterval(fetchSyncStatus, 10_000);
      return () => clearInterval(timer);
    }
  }, [isUpstoxConnected, fetchSyncStatus]);

  const connectors = BROKERS.map((b) => {
    if (b.id === "upstox" && isUpstoxConnected) {
      return { ...b, status: "connected" as const };
    }
    return b;
  });

  const configured = connectors.filter((c) => c.status === "connected");
  const available = connectors.filter((c) => c.status !== "connected");

  const handleConnect = (broker: BrokerConnector) => {
    if (broker.authUrl) {
      window.location.href = broker.authUrl;
    }
  };

  const handleDisconnect = async () => {
    try {
      await fetch("/api/v1/auth/logout", { method: "POST" });
      refreshAuth();
    } catch {
      /* ignore */
    }
  };

  if (authLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="text-slate-400 text-sm">
          Checking connections...
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">Connectors</h2>
          <p className="text-sm text-slate-400">
            Link your brokerage accounts to trade and analyze with real data
          </p>
        </div>
        <button
          onClick={refreshAuth}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-700 text-slate-300 text-xs hover:bg-slate-600 transition-colors"
        >
          <RefreshCw size={12} />
          Refresh
        </button>
      </div>

      {configured.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
            <h3 className="text-xs font-medium text-slate-500 uppercase tracking-wider">
              Configured ({configured.length})
            </h3>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {configured.map((c) => (
              <ConnectorCard
                key={c.id}
                connector={c}
                auth={authStatus}
                syncStatus={c.id === "upstox" ? syncStatus : null}
                onConnect={() => handleConnect(c)}
                onDisconnect={handleDisconnect}
                onRefreshAuth={refreshAuth}
              />
            ))}
          </div>
        </div>
      )}

      <div>
        <div className="flex items-center gap-2 mb-3">
          <h3 className="text-xs font-medium text-slate-500 uppercase tracking-wider">
            Available Brokers ({available.length})
          </h3>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {available.map((c) => (
            <ConnectorCard
              key={c.id}
              connector={c}
              auth={null}
              syncStatus={null}
              onConnect={() => handleConnect(c)}
              onDisconnect={handleDisconnect}
              onRefreshAuth={refreshAuth}
            />
          ))}
        </div>
      </div>

      <div className="bg-slate-800/20 rounded-lg border border-slate-700/30 p-4 text-xs text-slate-500">
        <strong className="text-slate-400">How it works:</strong> Click
        "Connect" to authenticate via your broker's OAuth page. Your access
        token is encrypted at rest (Fernet) and auto-refreshes. OptiTrade Pro
        is read-only &mdash; no orders are placed through this platform. Market
        data and portfolio positions sync automatically during market hours.
      </div>
    </div>
  );
}
