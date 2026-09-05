interface Position {
  strike: number;
  expiry: number;
  vol: number;
  optionType: string;
  price: number;
  greeks: {
    delta: number;
    gamma: number;
    vega: number;
    theta: number;
    rho: number;
    vanna: number;
    volga: number;
  };
}

interface Props {
  data: {
    spot: number;
    rate: number;
    positions: Position[];
  };
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

export function GreeksBook({ data }: Props) {
  const agg = data.positions.reduce(
    (acc, p) => {
      acc.delta += p.greeks.delta;
      acc.gamma += p.greeks.gamma;
      acc.vega += p.greeks.vega;
      acc.theta += p.greeks.theta;
      acc.rho += p.greeks.rho;
      acc.vanna += p.greeks.vanna;
      acc.volga += p.greeks.volga;
      acc.notional += p.price;
      return acc;
    },
    { delta: 0, gamma: 0, vega: 0, theta: 0, rho: 0, vanna: 0, volga: 0, notional: 0 }
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">Greeks Book</h2>
          <p className="text-sm text-slate-400">
            {data.positions.length} legs — analytic Black&ndash;Scholes Greeks.
            The engine is cross-validated against adjoint AD, JAX autodiff and
            finite differences in <code>test_greeks_cross.py</code>.
          </p>
        </div>
        <div className="text-right text-xs text-slate-400">
          <div>
            Spot:{" "}
            <span className="text-emerald-400 font-mono">
              {data.spot.toLocaleString()}
            </span>
          </div>
          <div className="text-slate-500">
            Rate: {(data.rate * 100).toFixed(1)}%
          </div>
        </div>
      </div>

      <div className="grid grid-cols-7 gap-3">
        {(
          [
            ["Delta", agg.delta],
            ["Gamma", agg.gamma],
            ["Vega", agg.vega],
            ["Theta", agg.theta],
            ["Rho", agg.rho],
            ["Vanna", agg.vanna],
            ["Volga", agg.volga],
          ] as [string, number][]
        ).map(([label, value]) => (
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

      <div className="bg-slate-800/50 rounded-xl border border-slate-700 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-700 text-xs text-slate-500">
                <th className="py-2.5 px-3 text-left font-medium">Type</th>
                <th className="py-2.5 px-3 text-right font-medium">Strike</th>
                <th className="py-2.5 px-3 text-right font-medium">Expiry</th>
                <th className="py-2.5 px-3 text-right font-medium">Vol</th>
                <th className="py-2.5 px-3 text-right font-medium">Price</th>
                <th className="py-2.5 px-3 text-right font-medium">Delta</th>
                <th className="py-2.5 px-3 text-right font-medium">Gamma</th>
                <th className="py-2.5 px-3 text-right font-medium">Vega</th>
                <th className="py-2.5 px-3 text-right font-medium">Theta</th>
                <th className="py-2.5 px-3 text-right font-medium">Vanna</th>
                <th className="py-2.5 px-3 text-right font-medium">Volga</th>
              </tr>
            </thead>
            <tbody>
              {data.positions.map((p, i) => (
                <tr
                  key={i}
                  className="border-b border-slate-800 hover:bg-slate-800/50 transition-colors"
                >
                  <td className="py-2 px-3">
                    <span
                      className={`text-xs font-medium px-2 py-0.5 rounded ${
                        p.optionType === "call"
                          ? "bg-emerald-900/30 text-emerald-400"
                          : "bg-red-900/30 text-red-400"
                      }`}
                    >
                      {p.optionType.toUpperCase()}
                    </span>
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-xs text-slate-300">
                    {p.strike.toLocaleString()}
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-xs text-slate-400">
                    {(p.expiry * 365).toFixed(0)}d
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-xs text-slate-400">
                    {(p.vol * 100).toFixed(1)}%
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-xs text-slate-200">
                    ₹{p.price.toFixed(1)}
                  </td>
                  <td
                    className={`py-2 px-3 text-right font-mono text-xs ${colorClass(
                      p.greeks.delta
                    )}`}
                  >
                    {formatNum(p.greeks.delta)}
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-xs text-slate-400">
                    {formatNum(p.greeks.gamma, 6)}
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-xs text-slate-400">
                    {formatNum(p.greeks.vega, 2)}
                  </td>
                  <td
                    className={`py-2 px-3 text-right font-mono text-xs ${colorClass(
                      p.greeks.theta
                    )}`}
                  >
                    {formatNum(p.greeks.theta, 2)}
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-xs text-slate-400">
                    {formatNum(p.greeks.vanna, 4)}
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-xs text-slate-400">
                    {formatNum(p.greeks.volga, 2)}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-slate-600 bg-slate-800/80 font-medium">
                <td className="py-2.5 px-3 text-xs text-slate-300" colSpan={4}>
                  BOOK TOTAL
                </td>
                <td className="py-2.5 px-3 text-right font-mono text-xs text-slate-200">
                  ₹{agg.notional.toFixed(1)}
                </td>
                <td
                  className={`py-2.5 px-3 text-right font-mono text-xs ${colorClass(
                    agg.delta
                  )}`}
                >
                  {formatNum(agg.delta)}
                </td>
                <td className="py-2.5 px-3 text-right font-mono text-xs text-slate-300">
                  {formatNum(agg.gamma, 6)}
                </td>
                <td className="py-2.5 px-3 text-right font-mono text-xs text-slate-300">
                  {formatNum(agg.vega, 2)}
                </td>
                <td
                  className={`py-2.5 px-3 text-right font-mono text-xs ${colorClass(
                    agg.theta
                  )}`}
                >
                  {formatNum(agg.theta, 2)}
                </td>
                <td className="py-2.5 px-3 text-right font-mono text-xs text-slate-300">
                  {formatNum(agg.vanna, 4)}
                </td>
                <td className="py-2.5 px-3 text-right font-mono text-xs text-slate-300">
                  {formatNum(agg.volga, 2)}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
    </div>
  );
}
