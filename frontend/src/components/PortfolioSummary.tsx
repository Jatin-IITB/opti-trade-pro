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

  const loginUrl = `/api/v1/auth/login?return_url=${encodeURIComponent(window.location.origin)}`;

  if (error) {
    // 401/403 mean no valid Upstox session; 503 means the sync service has no
    // stored token yet. All three are "log in", not "retry".
    const isAuthError = /\b(401|403|503)\b/.test(error);
    return (
      <div className="space-y-4">
        <h2 className="text-xl font-semibold text-slate-100">Portfolio</h2>
        <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-8 text-center">
          <div className="text-amber-400 text-sm mb-2">
            {isAuthError ? "Not Connected" : "Portfolio sync error"}
          </div>
          <div className="text-slate-500 text-xs mb-4">{error}</div>
          {isAuthError ? (
            <p className="text-slate-400 text-xs">
              Authenticate via{" "}
              <a href={loginUrl} className="text-blue-400 underline">
                Upstox login
              </a>{" "}
              to connect your portfolio.
            </p>
          ) : (
            <button
              onClick={onRefresh}
              className="px-3 py-1.5 rounded-lg bg-slate-700 text-slate-300 text-xs hover:bg-slate-600 transition-colors"
            >
              Retry
            </button>
          )}
        </div>
      </div>
    );
  }

  if (!summary || !summary.synced) {
    return (
      <div className="space-y-4">
        <h2 className="text-xl font-semibold text-slate-100">Portfolio</h2>
        <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-8 text-center">
          <div className="text-blue-400 text-sm mb-2">Syncing portfolio...</div>
          <p className="text-slate-400 text-xs">
            Fetching your positions from Upstox. This usually takes a few
            seconds.
          </p>
        </div>
      </div>
    );
  }

  const greeks = summary.aggregate_greeks;
  const greekCards: [string, number][] = greeks
    ? [
        ["Delta", greeks.delta],
        ["Gamma", greeks.gamma],
        ["Vega", greeks.vega],
        ["Theta", greeks.theta],
        ["Rho", greeks.rho],
        ["Vanna", greeks.vanna],
        ["Volga", greeks.volga],
      ]
    : [];

  const inr = (v: number) => `₹${v.toLocaleString("en-IN")}`;
  // A null field means the input is unavailable, not that the value is zero.
  const opt = (v: number | null, fmt: (n: number) => string) =>
    v === null || v === undefined ? "—" : fmt(v);

  return (
    <div className="space-y-4">
      {syncStatus?.auth_required && (
        <div className="rounded-lg border border-amber-700/50 bg-amber-950/40 px-4 py-3">
          <div className="text-amber-300 text-sm font-medium mb-1">
            Upstox session expired
          </div>
          <p className="text-amber-200/80 text-xs">
            The figures below are from the last successful sync and are no
            longer updating. Upstox access tokens expire daily.{" "}
            <a href={loginUrl} className="text-blue-400 underline">
              Log in again
            </a>{" "}
            to resume.
          </p>
        </div>
      )}

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
            {opt(summary.equity, inr)}
          </div>
          <div className="text-xs text-slate-500 mt-1">
            {summary.margin_utilization === null
              ? "margin unavailable"
              : `${(summary.margin_utilization * 100).toFixed(1)}% margin used`}
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
        <div className="flex items-baseline justify-between mb-2">
          <div className="text-xs text-slate-500">
            Portfolio Aggregate Greeks
          </div>
          {greeks && (
            <div className="text-xs text-slate-600">
              {summary.greeks_priced} of {summary.core_positions} F&O legs
              priced at spot {opt(summary.spot, (v) => v.toLocaleString("en-IN"))}
            </div>
          )}
        </div>
        {greeks ? (
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
        ) : (
          <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-6 text-center">
            <div className="text-amber-400 text-sm mb-1">
              Awaiting live market data
            </div>
            <p className="text-slate-400 text-xs">
              Book Greeks need a live underlying price. The capture schedule
              supplies it on its next cycle — your positions and P&amp;L above
              are unaffected.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
