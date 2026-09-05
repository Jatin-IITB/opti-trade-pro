import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle,
  AlertCircle,
  ExternalLink,
  RefreshCw,
  LogOut,
  Plug,
  Settings,
  X,
  Eye,
  EyeOff,
  Loader2,
} from "lucide-react";
import { useAuthStatus, type AuthStatus } from "../hooks/useAuthStatus";

interface BrokerConnector {
  id: string;
  name: string;
  description: string;
  status: "connected" | "configured" | "disconnected" | "coming_soon";
  logoText: string;
  logoGradient: string;
  features: string[];
  authUrl?: string;
}

interface FieldSchema {
  field: string;
  label: string;
  type: string;
  required: boolean;
  secret: boolean;
  hint?: string;
  default?: string;
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
  if (status === "configured") {
    return (
      <span className="flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full bg-blue-900/30 text-blue-400 border border-blue-700/50">
        <Settings size={12} />
        Configured
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

function SetupModal({
  broker,
  onClose,
  onSaved,
}: {
  broker: BrokerConnector;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [schema, setSchema] = useState<FieldSchema[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [showSecrets, setShowSecrets] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    success: boolean;
    message: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const resp = await fetch(`/api/v1/connectors/schema/${broker.id}`);
        if (!resp.ok) throw new Error("Failed to load schema");
        const data = await resp.json();
        setSchema(data.fields);
        const defaults: Record<string, string> = {};
        for (const f of data.fields) {
          if (f.default) defaults[f.field] = f.default;
        }

        const existing = await fetch(`/api/v1/connectors/${broker.id}`);
        if (existing.ok) {
          const cfg = await existing.json();
          if (cfg.configured && cfg.config) {
            for (const [k, v] of Object.entries(cfg.config)) {
              if (typeof v === "string") defaults[k] = v;
            }
          }
        }
        setValues(defaults);
      } catch {
        setError("Backend is not running. Start the server first.");
      }
    })();
  }, [broker.id]);

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      await fetch(`/api/v1/connectors/${broker.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values }),
      });
      const resp = await fetch(`/api/v1/connectors/${broker.id}/test`, {
        method: "POST",
      });
      const result = await resp.json();
      setTestResult(result);
    } catch {
      setTestResult({
        success: false,
        message: "Failed to reach backend.",
      });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const resp = await fetch(`/api/v1/connectors/${broker.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values }),
      });
      if (!resp.ok) {
        const data = await resp.json();
        throw new Error(data.detail || "Save failed");
      }
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-slate-800 rounded-2xl border border-slate-700 w-full max-w-lg mx-4 shadow-2xl">
        <div className="flex items-center justify-between p-5 border-b border-slate-700">
          <div className="flex items-center gap-3">
            <div
              className={`w-8 h-8 rounded-lg bg-gradient-to-br ${broker.logoGradient} flex items-center justify-center text-white font-bold text-xs`}
            >
              {broker.logoText}
            </div>
            <div>
              <h3 className="text-sm font-semibold text-slate-100">
                Configure {broker.name}
              </h3>
              <p className="text-xs text-slate-500">
                Enter your API credentials from the developer console
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-700 transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {error && (
            <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-900/20 border border-red-800/30 text-red-400 text-xs">
              <AlertCircle size={14} className="shrink-0" />
              {error}
            </div>
          )}

          {schema.map((field) => (
            <div key={field.field}>
              <label className="block text-xs font-medium text-slate-300 mb-1.5">
                {field.label}
                {field.required && (
                  <span className="text-red-400 ml-0.5">*</span>
                )}
              </label>
              <div className="relative">
                <input
                  type={
                    field.secret && !showSecrets[field.field]
                      ? "password"
                      : "text"
                  }
                  value={values[field.field] || ""}
                  onChange={(e) =>
                    setValues((v) => ({
                      ...v,
                      [field.field]: e.target.value,
                    }))
                  }
                  placeholder={field.hint}
                  className="w-full px-3 py-2 rounded-lg bg-slate-900 border border-slate-600 text-sm text-slate-200 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500/30 font-mono transition-colors"
                />
                {field.secret && (
                  <button
                    type="button"
                    onClick={() =>
                      setShowSecrets((s) => ({
                        ...s,
                        [field.field]: !s[field.field],
                      }))
                    }
                    className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-slate-500 hover:text-slate-300 transition-colors"
                  >
                    {showSecrets[field.field] ? (
                      <EyeOff size={14} />
                    ) : (
                      <Eye size={14} />
                    )}
                  </button>
                )}
              </div>
              {field.hint && (
                <p className="text-[10px] text-slate-600 mt-1">{field.hint}</p>
              )}
            </div>
          ))}

          {testResult && (
            <div
              className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs ${
                testResult.success
                  ? "bg-emerald-900/20 border border-emerald-800/30 text-emerald-400"
                  : "bg-red-900/20 border border-red-800/30 text-red-400"
              }`}
            >
              {testResult.success ? (
                <CheckCircle size={14} />
              ) : (
                <AlertCircle size={14} />
              )}
              {testResult.message}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between p-5 border-t border-slate-700">
          <button
            onClick={handleTest}
            disabled={testing}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-slate-700 text-slate-300 text-xs font-medium hover:bg-slate-600 active:scale-95 transition-all disabled:opacity-50"
          >
            {testing ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <RefreshCw size={12} />
            )}
            {testing ? "Testing..." : "Test Connection"}
          </button>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 rounded-lg text-slate-400 text-xs font-medium hover:text-slate-200 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-blue-600 text-white text-xs font-medium hover:bg-blue-500 active:scale-95 transition-all disabled:opacity-50"
            >
              {saving && <Loader2 size={12} className="animate-spin" />}
              Save & Continue
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ConnectorCard({
  connector,
  auth,
  syncStatus,
  onSetup,
  onConnect,
  onDisconnect,
  onRefreshAuth,
}: {
  connector: BrokerConnector;
  auth: AuthStatus | null;
  syncStatus: SyncStatus | null;
  onSetup: () => void;
  onConnect: () => void;
  onDisconnect: () => void;
  onRefreshAuth: () => void;
}) {
  const isConnected = connector.status === "connected";
  const isConfigured = connector.status === "configured";
  const isComingSoon = connector.status === "coming_soon";

  return (
    <div
      className={`bg-slate-800/50 rounded-xl border p-5 transition-colors ${
        isConnected
          ? "border-emerald-700/50"
          : isConfigured
            ? "border-blue-700/50"
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
              onClick={onSetup}
              className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-slate-700 text-slate-300 text-xs font-medium hover:bg-slate-600 hover:text-slate-100 active:scale-95 transition-all duration-150"
            >
              <Settings size={12} />
            </button>
            <button
              onClick={onRefreshAuth}
              className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-slate-700 text-slate-300 text-xs font-medium hover:bg-slate-600 hover:text-slate-100 active:scale-95 transition-all duration-150"
            >
              <RefreshCw size={12} />
              Refresh Token
            </button>
            <button
              onClick={onDisconnect}
              className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-red-900/20 text-red-400 text-xs font-medium hover:bg-red-900/40 active:scale-95 transition-all duration-150 border border-red-800/30"
            >
              <LogOut size={12} />
            </button>
          </>
        ) : isConfigured ? (
          <>
            <button
              onClick={onConnect}
              className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-blue-600 text-white text-xs font-medium hover:bg-blue-500 active:scale-95 transition-all duration-150"
            >
              <ExternalLink size={12} />
              Login with {connector.name}
            </button>
            <button
              onClick={onSetup}
              className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-slate-700 text-slate-300 text-xs font-medium hover:bg-slate-600 hover:text-slate-100 active:scale-95 transition-all duration-150"
            >
              <Settings size={12} />
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
            onClick={onSetup}
            className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-blue-600 text-white text-xs font-medium hover:bg-blue-500 active:scale-95 transition-all duration-150"
          >
            <Settings size={12} />
            Setup {connector.name}
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
  const {
    status: authStatus,
    loading: authLoading,
    refresh: refreshAuth,
  } = useAuthStatus();
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [setupBroker, setSetupBroker] = useState<BrokerConnector | null>(null);
  const [connectorStates, setConnectorStates] = useState<
    Record<string, boolean>
  >({});

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    refreshAuth();
    await fetchConnectorStates();
    setTimeout(() => setRefreshing(false), 800);
  }, [refreshAuth]);

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

  const fetchConnectorStates = useCallback(async () => {
    try {
      const resp = await fetch("/api/v1/connectors/");
      if (resp.ok) {
        const data = await resp.json();
        const states: Record<string, boolean> = {};
        for (const c of data) {
          states[c.broker_id] = c.configured;
        }
        setConnectorStates(states);
      }
    } catch {
      /* backend may not be running */
    }
  }, []);

  useEffect(() => {
    fetchConnectorStates();
  }, [fetchConnectorStates]);

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
    if (connectorStates[b.id] && b.status !== "coming_soon") {
      return { ...b, status: "configured" as const };
    }
    return b;
  });

  const configured = connectors.filter(
    (c) => c.status === "connected" || c.status === "configured",
  );
  const available = connectors.filter(
    (c) => c.status !== "connected" && c.status !== "configured",
  );

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
        <div className="text-slate-400 text-sm">Checking connections...</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {setupBroker && (
        <SetupModal
          broker={setupBroker}
          onClose={() => setSetupBroker(null)}
          onSaved={() => {
            setSetupBroker(null);
            fetchConnectorStates();
            refreshAuth();
          }}
        />
      )}

      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">Connectors</h2>
          <p className="text-sm text-slate-400">
            Link your brokerage accounts to trade and analyze with real data
          </p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-200 ${
            refreshing
              ? "bg-emerald-900/30 text-emerald-400 border border-emerald-700/50"
              : "bg-slate-700 text-slate-300 hover:bg-slate-600 hover:text-slate-100 active:scale-95"
          }`}
        >
          <RefreshCw
            size={12}
            className={refreshing ? "animate-spin" : ""}
          />
          {refreshing ? "Refreshed" : "Refresh"}
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
                auth={c.status === "connected" ? authStatus : null}
                syncStatus={
                  c.id === "upstox" && c.status === "connected"
                    ? syncStatus
                    : null
                }
                onSetup={() => setSetupBroker(c)}
                onConnect={() => handleConnect(c)}
                onDisconnect={handleDisconnect}
                onRefreshAuth={refreshAuth}
              />
            ))}
          </div>
        </div>
      )}

      {available.length > 0 && (
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
                onSetup={() => setSetupBroker(c)}
                onConnect={() => handleConnect(c)}
                onDisconnect={handleDisconnect}
                onRefreshAuth={refreshAuth}
              />
            ))}
          </div>
        </div>
      )}

      <div className="bg-slate-800/20 rounded-lg border border-slate-700/30 p-4 text-xs text-slate-500">
        <strong className="text-slate-400">How it works:</strong> Click
        &ldquo;Setup&rdquo; to enter your API credentials from your broker&apos;s
        developer console. Credentials are encrypted at rest (Fernet). Then click
        &ldquo;Login&rdquo; to authenticate via OAuth. OptiTrade Pro is
        read-only &mdash; no orders are placed. Portfolio positions sync
        automatically during market hours.
      </div>
    </div>
  );
}
