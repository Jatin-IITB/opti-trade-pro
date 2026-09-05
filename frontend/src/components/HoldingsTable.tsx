import type { Holding } from "../hooks/usePortfolio";

interface Props {
  holdings: Holding[];
}

function colorClass(v: number): string {
  if (v > 0) return "text-emerald-400";
  if (v < 0) return "text-red-400";
  return "text-slate-400";
}

function inr(v: number): string {
  return `₹${v.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function signed(v: number): string {
  return `${v >= 0 ? "+" : ""}${inr(v)}`;
}

export function HoldingsTable({ holdings }: Props) {
  if (holdings.length === 0) {
    return (
      <div className="space-y-3">
        <h3 className="text-lg font-semibold text-slate-100">Holdings</h3>
        <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-6 text-center">
          <p className="text-slate-400 text-sm">
            No long-term equity holdings in this account.
          </p>
        </div>
      </div>
    );
  }

  const totalValue = holdings.reduce(
    (sum, h) => sum + h.last_price * h.quantity,
    0,
  );
  const totalInvested = holdings.reduce(
    (sum, h) => sum + h.average_price * h.quantity,
    0,
  );
  const totalPnl = holdings.reduce((sum, h) => sum + h.pnl, 0);
  const totalDayChange = holdings.reduce(
    (sum, h) => sum + h.day_change * h.quantity,
    0,
  );
  const totalPnlPct = totalInvested > 0 ? (totalPnl / totalInvested) * 100 : 0;

  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between">
        <h3 className="text-lg font-semibold text-slate-100">Holdings</h3>
        <span className="text-xs text-slate-500">
          {holdings.length} instrument{holdings.length === 1 ? "" : "s"} &middot;
          long-term equity
        </span>
      </div>

      <div className="grid grid-cols-4 gap-3">
        <div className="bg-slate-800/50 rounded-lg border border-slate-700 p-4">
          <div className="text-xs text-slate-500">Current Value</div>
          <div className="text-xl font-mono text-slate-100 mt-1">
            {inr(totalValue)}
          </div>
        </div>
        <div className="bg-slate-800/50 rounded-lg border border-slate-700 p-4">
          <div className="text-xs text-slate-500">Invested</div>
          <div className="text-xl font-mono text-slate-300 mt-1">
            {inr(totalInvested)}
          </div>
        </div>
        <div className="bg-slate-800/50 rounded-lg border border-slate-700 p-4">
          <div className="text-xs text-slate-500">Total P&amp;L</div>
          <div className={`text-xl font-mono mt-1 ${colorClass(totalPnl)}`}>
            {signed(totalPnl)}
          </div>
          <div className={`text-xs mt-1 ${colorClass(totalPnl)}`}>
            {totalPnlPct >= 0 ? "+" : ""}
            {totalPnlPct.toFixed(2)}%
          </div>
        </div>
        <div className="bg-slate-800/50 rounded-lg border border-slate-700 p-4">
          <div className="text-xs text-slate-500">Day Change</div>
          <div
            className={`text-xl font-mono mt-1 ${colorClass(totalDayChange)}`}
          >
            {signed(totalDayChange)}
          </div>
        </div>
      </div>

      <div className="bg-slate-800/50 rounded-xl border border-slate-700 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700 text-slate-500 text-xs">
              <th className="text-left font-medium px-4 py-3">Symbol</th>
              <th className="text-right font-medium px-4 py-3">Qty</th>
              <th className="text-right font-medium px-4 py-3">Avg Cost</th>
              <th className="text-right font-medium px-4 py-3">LTP</th>
              <th className="text-right font-medium px-4 py-3">Value</th>
              <th className="text-right font-medium px-4 py-3">P&amp;L</th>
              <th className="text-right font-medium px-4 py-3">P&amp;L %</th>
              <th className="text-right font-medium px-4 py-3">Day</th>
            </tr>
          </thead>
          <tbody>
            {holdings.map((h) => {
              const invested = h.average_price * h.quantity;
              const value = h.last_price * h.quantity;
              const pnlPct = invested > 0 ? (h.pnl / invested) * 100 : 0;
              return (
                <tr
                  key={h.instrument_key || h.trading_symbol}
                  className="border-b border-slate-800 last:border-0 hover:bg-slate-800/40 transition-colors"
                >
                  <td className="px-4 py-3">
                    <div className="text-slate-200 font-medium">
                      {h.trading_symbol}
                    </div>
                    <div className="text-xs text-slate-500">{h.exchange}</div>
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-slate-300">
                    {h.quantity}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-slate-400">
                    {inr(h.average_price)}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-slate-200">
                    {inr(h.last_price)}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-slate-300">
                    {inr(value)}
                  </td>
                  <td
                    className={`px-4 py-3 text-right font-mono ${colorClass(h.pnl)}`}
                  >
                    {signed(h.pnl)}
                  </td>
                  <td
                    className={`px-4 py-3 text-right font-mono ${colorClass(h.pnl)}`}
                  >
                    {pnlPct >= 0 ? "+" : ""}
                    {pnlPct.toFixed(2)}%
                  </td>
                  <td
                    className={`px-4 py-3 text-right font-mono ${colorClass(h.day_change)}`}
                  >
                    {h.day_change_percentage >= 0 ? "+" : ""}
                    {h.day_change_percentage.toFixed(2)}%
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="bg-slate-800/60 font-medium">
              <td className="px-4 py-3 text-slate-300">TOTAL</td>
              <td colSpan={3} />
              <td className="px-4 py-3 text-right font-mono text-slate-200">
                {inr(totalValue)}
              </td>
              <td
                className={`px-4 py-3 text-right font-mono ${colorClass(totalPnl)}`}
              >
                {signed(totalPnl)}
              </td>
              <td
                className={`px-4 py-3 text-right font-mono ${colorClass(totalPnl)}`}
              >
                {totalPnlPct >= 0 ? "+" : ""}
                {totalPnlPct.toFixed(2)}%
              </td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}
