import type { PortfolioSignal } from "../hooks/usePortfolio";

interface Props {
  signals: PortfolioSignal[];
}

function pnlColor(v: number): string {
  if (v > 0) return "text-emerald-400";
  if (v < 0) return "text-red-400";
  return "text-slate-400";
}

function moneynessStyle(m: string): string {
  switch (m) {
    case "ITM":
      return "bg-emerald-900/30 text-emerald-400";
    case "OTM":
      return "bg-red-900/30 text-red-400";
    case "ATM":
      return "bg-blue-900/30 text-blue-400";
    default:
      return "bg-slate-800 text-slate-400";
  }
}

function dteColor(dte: number | null): string {
  if (dte === null) return "text-slate-500";
  if (dte <= 3) return "text-red-400";
  if (dte <= 7) return "text-amber-400";
  return "text-slate-300";
}

export function PositionSignals({ signals }: Props) {
  if (signals.length === 0) {
    return (
      <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-8 text-center">
        <div className="text-slate-400 text-sm">No positions to display</div>
      </div>
    );
  }

  const totalPnl = signals.reduce((s, p) => s + p.pnl, 0);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-slate-100">
            Position Signals
          </h3>
          <p className="text-sm text-slate-400">
            {signals.length} positions &middot; Entry vs current, moneyness,
            time decay
          </p>
        </div>
        <div className="text-right text-xs">
          <div className="text-slate-500">Book P&L</div>
          <div className={`text-lg font-mono ${pnlColor(totalPnl)}`}>
            ₹{totalPnl.toLocaleString("en-IN")}
          </div>
        </div>
      </div>

      <div className="bg-slate-800/50 rounded-xl border border-slate-700 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-700 text-xs text-slate-500">
                <th className="py-2.5 px-3 text-left font-medium">Symbol</th>
                <th className="py-2.5 px-3 text-center font-medium">Type</th>
                <th className="py-2.5 px-3 text-right font-medium">Strike</th>
                <th className="py-2.5 px-3 text-right font-medium">Qty</th>
                <th className="py-2.5 px-3 text-right font-medium">Entry</th>
                <th className="py-2.5 px-3 text-right font-medium">
                  Current
                </th>
                <th className="py-2.5 px-3 text-right font-medium">P&L</th>
                <th className="py-2.5 px-3 text-right font-medium">P&L %</th>
                <th className="py-2.5 px-3 text-center font-medium">
                  Moneyness
                </th>
                <th className="py-2.5 px-3 text-right font-medium">DTE</th>
              </tr>
            </thead>
            <tbody>
              {signals.map((s, i) => (
                <tr
                  key={i}
                  className="border-b border-slate-800 hover:bg-slate-800/50 transition-colors"
                >
                  <td className="py-2 px-3 font-mono text-xs text-slate-200">
                    {s.trading_symbol}
                  </td>
                  <td className="py-2 px-3 text-center">
                    {s.option_type && (
                      <span
                        className={`text-xs font-medium px-2 py-0.5 rounded ${
                          s.option_type === "CE"
                            ? "bg-emerald-900/30 text-emerald-400"
                            : "bg-red-900/30 text-red-400"
                        }`}
                      >
                        {s.option_type}
                      </span>
                    )}
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-xs text-slate-300">
                    {s.strike_price?.toLocaleString() ?? "—"}
                  </td>
                  <td
                    className={`py-2 px-3 text-right font-mono text-xs ${
                      s.quantity >= 0 ? "text-emerald-400" : "text-red-400"
                    }`}
                  >
                    {s.quantity > 0 ? "+" : ""}
                    {s.quantity}
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-xs text-slate-400">
                    ₹{s.entry_price.toFixed(1)}
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-xs text-slate-200">
                    ₹{s.current_price.toFixed(1)}
                  </td>
                  <td
                    className={`py-2 px-3 text-right font-mono text-xs ${pnlColor(s.pnl)}`}
                  >
                    {s.pnl >= 0 ? "+" : ""}₹{s.pnl.toLocaleString("en-IN")}
                  </td>
                  <td
                    className={`py-2 px-3 text-right font-mono text-xs ${pnlColor(s.pnl_pct)}`}
                  >
                    {s.pnl_pct >= 0 ? "+" : ""}
                    {s.pnl_pct.toFixed(1)}%
                  </td>
                  <td className="py-2 px-3 text-center">
                    <span
                      className={`text-xs font-medium px-2 py-0.5 rounded ${moneynessStyle(s.moneyness)}`}
                    >
                      {s.moneyness}
                    </span>
                  </td>
                  <td
                    className={`py-2 px-3 text-right font-mono text-xs ${dteColor(s.days_to_expiry)}`}
                  >
                    {s.days_to_expiry !== null ? `${s.days_to_expiry}d` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-slate-600 bg-slate-800/80 font-medium">
                <td
                  className="py-2.5 px-3 text-xs text-slate-300"
                  colSpan={6}
                >
                  TOTAL
                </td>
                <td
                  className={`py-2.5 px-3 text-right font-mono text-xs ${pnlColor(totalPnl)}`}
                >
                  {totalPnl >= 0 ? "+" : ""}₹
                  {totalPnl.toLocaleString("en-IN")}
                </td>
                <td colSpan={3} />
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
    </div>
  );
}
