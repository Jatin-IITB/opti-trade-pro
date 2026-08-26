import Plot from "react-plotly.js";

interface ExpirySlice {
  t: number;
  strikes: number[];
  marketVols: number[];
  fittedVols: number[];
  theta: number;
  rmse: number;
}

interface Props {
  data: {
    expiries: ExpirySlice[];
    spot: number;
    params: { rho: number; eta: number; gamma: number };
    durrlemanViolations: number;
  };
}

const COLORS = ["#f59e0b", "#3b82f6", "#10b981"];

export function EssviCalibration({ data }: Props) {
  const traces = data.expiries.flatMap((slice, i) => [
    {
      type: "scatter" as const,
      x: slice.strikes,
      y: slice.marketVols.map((v) => v * 100),
      mode: "markers" as const,
      name: `Market ${(slice.t * 365).toFixed(0)}d`,
      marker: { color: COLORS[i], size: 6, opacity: 0.6 },
      hovertemplate: `Strike: %{x:,.0f}<br>Market IV: %{y:.2f}%<extra>${(slice.t * 365).toFixed(0)}d</extra>`,
    },
    {
      type: "scatter" as const,
      x: slice.strikes,
      y: slice.fittedVols.map((v) => v * 100),
      mode: "lines" as const,
      name: `eSSVI ${(slice.t * 365).toFixed(0)}d`,
      line: { color: COLORS[i], width: 2 },
      hovertemplate: `Strike: %{x:,.0f}<br>Fitted IV: %{y:.2f}%<extra>${(slice.t * 365).toFixed(0)}d</extra>`,
    },
  ]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">
            eSSVI Joint Calibration
          </h2>
          <p className="text-sm text-slate-400">
            Gatheral&ndash;Jacquier SSVI fitted jointly across{" "}
            {data.expiries.length} expiries with butterfly penalties
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div
            className={`px-3 py-1.5 rounded-lg border ${
              data.durrlemanViolations === 0
                ? "bg-emerald-900/30 border-emerald-700/50 text-emerald-400"
                : "bg-red-900/30 border-red-700/50 text-red-400"
            }`}
          >
            <span className="text-xs font-mono">
              {data.durrlemanViolations} Durrleman violations
            </span>
          </div>
        </div>
      </div>

      <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-4">
        <Plot
          data={traces}
          layout={{
            autosize: true,
            height: 440,
            margin: { l: 60, r: 20, t: 20, b: 50 },
            paper_bgcolor: "transparent",
            plot_bgcolor: "transparent",
            xaxis: {
              title: { text: "Strike", font: { color: "#94a3b8" } },
              gridcolor: "#1e293b",
              color: "#94a3b8",
              tickformat: ",",
            },
            yaxis: {
              title: { text: "Implied Vol (%)", font: { color: "#94a3b8" } },
              gridcolor: "#1e293b",
              color: "#94a3b8",
              ticksuffix: "%",
            },
            legend: {
              font: { color: "#94a3b8", size: 11 },
              bgcolor: "transparent",
              x: 0.02,
              y: 0.98,
            },
            font: { color: "#94a3b8" },
          }}
          config={{ responsive: true }}
          className="w-full"
        />
      </div>

      <div className="grid grid-cols-3 gap-4">
        {data.expiries.map((slice) => (
          <div
            key={slice.t}
            className="bg-slate-800/30 rounded-lg border border-slate-700/50 p-4"
          >
            <div className="text-sm font-medium text-slate-300">
              {(slice.t * 365).toFixed(0)}-day expiry
            </div>
            <div className="grid grid-cols-2 gap-2 mt-2">
              <div>
                <div className="text-xs text-slate-500">&theta;</div>
                <div className="text-sm font-mono text-slate-200">
                  {slice.theta.toFixed(4)}
                </div>
              </div>
              <div>
                <div className="text-xs text-slate-500">RMSE</div>
                <div className="text-sm font-mono text-emerald-400">
                  {(slice.rmse * 100).toFixed(2)} vol-pt
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-3 gap-4">
        {(
          [
            ["rho", data.params.rho, "Correlation — controls skew"],
            ["eta", data.params.eta, "ATM vol-of-vol scaling"],
            ["gamma", data.params.gamma, "Power-law decay exponent"],
          ] as [string, number, string][]
        ).map(([label, value, desc]) => (
          <div
            key={label}
            className="bg-slate-800/30 rounded-lg border border-slate-700/50 p-3"
          >
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-slate-300">
                {label}
              </span>
              <span className="text-sm font-mono text-slate-100">
                {value.toFixed(3)}
              </span>
            </div>
            <div className="text-xs text-slate-500 mt-1">{desc}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
