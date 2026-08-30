import type { PortfolioSummary as Summary, SyncStatus } from "../hooks/usePortfolio";

interface Props {
  summary: Summary | null;
  syncStatus: SyncStatus | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}

function formatNum(v: number, decimals = 4): string {
  const abs = Math.abs(v);
  if (abs === 0) return "0";
  if (abs < 0.0001) return v.toExponential(2);
  return v.toFixed(decimals);
}

function colorClass(v: number): string {
  if (v > 0) return "text-emerald-400";
  if (v < 0) return "text-red-400";
  return "text-slate-400";
}

function formatTimestamp(ts: number | null): string {
  if (!ts) return "Never";
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function PortfolioSummaryPanel({
  summary,
  syncStatus,
  loading,
  error,
  onRefresh,
}: Props) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="text-slate-400 text-sm">Loading portfolio...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-4">
        <h2 className="text-xl font-semibold text-slate-100">Portfolio</h2>
        <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-8 text-center">
          <div className="text-amber-400 text-sm mb-2">
            Portfolio sync not available
          </div>
          <div className="text-slate-500 text-xs mb-4">{error}</div>
          <p className="text-slate-400 text-xs">
            Authenticate via{" "}
            <a href="/api/v1/auth/login" className="text-blue-400 underline">
              Upstox login
            </a>{" "}
            to connect your portfolio.
          </p>
        </div>
      </div>
    );
  }

  if (!summary || !summary.synced) {
    return (
      <div className="space-y-4">
        <h2 className="text-xl font-semibold text-slate-100">Portfolio</h2>
        <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-8 text-center">
          <div className="text-amber-400 text-sm mb-2">Not Connected</div>
          <p className="text-slate-400 text-xs">
            Authenticate via{" "}
            <a href="/api/v1/auth/login" className="text-blue-400 underline">
              Upstox login
            </a>{" "}
            to sync your live portfolio.
          </p>
        </div>
      </div>
    );
  }

  const greeks = summary.aggregate_greeks;
  const greekCards: [string, number][] = [
    ["Delta", greeks.delta],
    ["Gamma", greeks.gamma],
    ["Vega", greeks.vega],
    ["Theta", greeks.theta],
    ["Rho", greeks.rho],
    ["Vanna", greeks.vanna],
    ["Volga", greeks.volga],
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">Portfolio</h2>
          <p className="text-sm text-slate-400">
            {summary.total_positions} positions synced from Upstox
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={onRefresh}
            className="px-3 py-1.5 rounded-lg bg-slate-700 text-slate-300 text-xs hover:bg-slate-600 transition-colors"
          >
            Refresh
          </button>
          <div
            className={`px-3 py-1.5 rounded-lg border ${
              syncStatus?.running
                ? "bg-emerald-900/30 border-emerald-700/50"
                : "bg-amber-900/30 border-amber-700/50"
            }`}
          >
            <span
              className={`text-xs font-mono ${
                syncStatus?.running ? "text-emerald-400" : "text-amber-400"
              }`}
            >
              {syncStatus?.running ? "Synced" : "Paused"}
            </span>
          </div>
        </div>
      </div>

      {/* Key metrics */}
      <div className="grid grid-cols-4 gap-3">
        <div className="bg-slate-800/50 rounded-lg border border-slate-700 p-4">
          <div className="text-xs text-slate-500">Positions</div>
          <div className="text-2xl font-mono text-slate-100 mt-1">
            {summary.total_positions}
          </div>
          <div className="text-xs text-slate-500 mt-1">
            {summary.core_positions} F&O
          </div>
        </div>
        <div className="bg-slate-800/50 rounded-lg border border-slate-700 p-4">
          <div className="text-xs text-slate-500">Total P&L</div>
          <div
            className={`text-2xl font-mono mt-1 ${colorClass(summary.total_pnl)}`}
          >
            ₹{summary.total_pnl.toLocaleString("en-IN")}
          </div>
        </div>
        <div className="bg-slate-800/50 rounded-lg border border-slate-700 p-4">
          <div className="text-xs text-slate-500">Equity</div>
          <div className="text-2xl font-mono text-slate-100 mt-1">
            ₹{summary.equity.toLocaleString("en-IN")}
          </div>
        </div>
        <div className="bg-slate-800/50 rounded-lg border border-slate-700 p-4">
          <div className="text-xs text-slate-500">Last Sync</div>
          <div className="text-lg font-mono text-slate-300 mt-1">
            {formatTimestamp(syncStatus?.last_sync_ts ?? null)}
          </div>
          <div className="text-xs text-slate-500 mt-1">
            {syncStatus?.n_syncs ?? 0} syncs &middot;{" "}
            {syncStatus?.n_failures ?? 0} failures
          </div>
        </div>
      </div>

      {/* Aggregate Greeks */}
      <div>
        <div className="text-xs text-slate-500 mb-2">
          Portfolio Aggregate Greeks
        </div>
        <div className="grid grid-cols-7 gap-3">
          {greekCards.map(([label, value]) => (
            <div
              key={label}
              className="bg-slate-800/50 rounded-lg border border-slate-700 p-3 text-center"
            >
              <div className="text-xs text-slate-500">{label}</div>
              <div className={`text-lg font-mono mt-1 ${colorClass(value)}`}>
                {formatNum(value)}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
