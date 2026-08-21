interface ChainRow {
  strike: number;
  callPrice: number;
  putPrice: number;
  callDelta: number;
  putDelta: number;
  gamma: number;
  vega: number;
  iv: number;
  oi: number;
}

interface Props {
  data: {
    spot: number;
    expiry: number;
    chain: ChainRow[];
  };
}

export function OptionChain({ data }: Props) {
  const atmStrike = data.chain.reduce((closest, row) =>
    Math.abs(row.strike - data.spot) < Math.abs(closest.strike - data.spot)
      ? row
      : closest
  ).strike;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">Option Chain</h2>
          <p className="text-sm text-slate-400">
            {data.chain.length} strikes &mdash;{" "}
            {(data.expiry * 365).toFixed(0)}d expiry &mdash; ATM at{" "}
            {atmStrike.toLocaleString()}
          </p>
        </div>
        <div className="text-right text-xs text-slate-400">
          <div>
            Spot:{" "}
            <span className="text-emerald-400 font-mono">
              {data.spot.toLocaleString()}
            </span>
          </div>
        </div>
      </div>

      <div className="bg-slate-800/50 rounded-xl border border-slate-700 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-700">
                <th
                  colSpan={3}
                  className="text-center text-xs font-medium text-emerald-400 py-2 bg-emerald-900/10 border-r border-slate-700"
                >
                  CALLS
                </th>
                <th className="text-center text-xs font-medium text-slate-400 py-2 px-4 border-r border-slate-700">
                  STRIKE
                </th>
                <th
                  colSpan={3}
                  className="text-center text-xs font-medium text-red-400 py-2 bg-red-900/10 border-r border-slate-700"
                >
                  PUTS
                </th>
                <th
                  colSpan={3}
                  className="text-center text-xs font-medium text-slate-400 py-2"
                >
                  GREEKS
                </th>
              </tr>
              <tr className="border-b border-slate-700 text-xs text-slate-500">
                <th className="py-2 px-3 text-right font-medium">Price</th>
                <th className="py-2 px-3 text-right font-medium">Delta</th>
                <th className="py-2 px-3 text-right font-medium border-r border-slate-700">
                  OI
                </th>
                <th className="py-2 px-4 text-center font-medium border-r border-slate-700">
                  K
                </th>
                <th className="py-2 px-3 text-right font-medium">Price</th>
                <th className="py-2 px-3 text-right font-medium">Delta</th>
                <th className="py-2 px-3 text-right font-medium border-r border-slate-700">
                  OI
                </th>
                <th className="py-2 px-3 text-right font-medium">IV</th>
                <th className="py-2 px-3 text-right font-medium">Gamma</th>
                <th className="py-2 px-3 text-right font-medium">Vega</th>
              </tr>
            </thead>
            <tbody>
              {data.chain.map((row) => {
                const isATM = row.strike === atmStrike;
                const isITMCall = row.strike < data.spot;
                const isITMPut = row.strike > data.spot;

                return (
                  <tr
                    key={row.strike}
                    className={`border-b border-slate-800 hover:bg-slate-800/50 transition-colors ${
                      isATM ? "bg-blue-900/20 border-blue-800/30" : ""
                    }`}
                  >
                    <td
                      className={`py-2 px-3 text-right font-mono text-xs ${
                        isITMCall ? "text-emerald-300" : "text-slate-400"
                      }`}
                    >
                      {row.callPrice.toFixed(1)}
                    </td>
                    <td className="py-2 px-3 text-right font-mono text-xs text-slate-300">
                      {row.callDelta.toFixed(3)}
                    </td>
                    <td className="py-2 px-3 text-right font-mono text-xs text-slate-500 border-r border-slate-700">
                      {(row.oi / 1000).toFixed(0)}k
                    </td>
                    <td
                      className={`py-2 px-4 text-center font-mono text-xs font-medium border-r border-slate-700 ${
                        isATM ? "text-blue-400" : "text-slate-300"
                      }`}
                    >
                      {row.strike.toLocaleString()}
                      {isATM && (
                        <span className="ml-1 text-[10px] text-blue-500">
                          ATM
                        </span>
                      )}
                    </td>
                    <td
                      className={`py-2 px-3 text-right font-mono text-xs ${
                        isITMPut ? "text-red-300" : "text-slate-400"
                      }`}
                    >
                      {row.putPrice.toFixed(1)}
                    </td>
                    <td className="py-2 px-3 text-right font-mono text-xs text-slate-300">
                      {row.putDelta.toFixed(3)}
                    </td>
                    <td className="py-2 px-3 text-right font-mono text-xs text-slate-500 border-r border-slate-700">
                      {(row.oi / 1000).toFixed(0)}k
                    </td>
                    <td className="py-2 px-3 text-right font-mono text-xs text-amber-400">
                      {(row.iv * 100).toFixed(1)}%
                    </td>
                    <td className="py-2 px-3 text-right font-mono text-xs text-slate-400">
                      {row.gamma.toFixed(5)}
                    </td>
                    <td className="py-2 px-3 text-right font-mono text-xs text-slate-400">
                      {row.vega.toFixed(1)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div className="bg-slate-800/30 rounded-lg border border-slate-700/50 p-3">
          <div className="text-xs text-slate-500">Put/Call Ratio (by OI)</div>
          <div className="text-lg font-mono text-slate-200 mt-1">
            {(
              data.chain.reduce((s, r) => s + (r.strike > data.spot ? r.oi : 0), 0) /
              data.chain.reduce((s, r) => s + (r.strike < data.spot ? r.oi : 0), 0)
            ).toFixed(2)}
          </div>
        </div>
        <div className="bg-slate-800/30 rounded-lg border border-slate-700/50 p-3">
          <div className="text-xs text-slate-500">Max Pain Strike</div>
          <div className="text-lg font-mono text-slate-200 mt-1">
            {data.chain
              .reduce((max, r) => (r.oi > max.oi ? r : max))
              .strike.toLocaleString()}
          </div>
        </div>
        <div className="bg-slate-800/30 rounded-lg border border-slate-700/50 p-3">
          <div className="text-xs text-slate-500">ATM Straddle</div>
          <div className="text-lg font-mono text-slate-200 mt-1">
            ₹
            {data.chain
              .filter((r) => r.strike === atmStrike)
              .map((r) => (r.callPrice + r.putPrice).toFixed(1))[0] ?? "—"}
          </div>
        </div>
      </div>
    </div>
  );
}
