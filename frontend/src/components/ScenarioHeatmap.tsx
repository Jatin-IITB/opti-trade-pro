import Plot from "react-plotly.js";

interface Props {
  data: {
    spotShifts: number[];
    volShifts: number[];
    pnl: number[][];
    strike: number;
    expiry: number;
    basePrice: number;
  };
}

export function ScenarioHeatmap({ data }: Props) {
  const maxAbs = Math.max(
    ...data.pnl.flat().map((v) => Math.abs(v))
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">
            Scenario P&amp;L Heatmap
          </h2>
          <p className="text-sm text-slate-400">
            {data.spotShifts.length} &times; {data.volShifts.length} grid &mdash;
            spot shift vs vol shift for a {data.strike.toLocaleString()} call,{" "}
            {(data.expiry * 365).toFixed(0)}d expiry
          </p>
        </div>
        <div className="text-right text-xs text-slate-400">
          <div>Base price: &#8377;{data.basePrice.toLocaleString()}</div>
          <div className="text-slate-500">
            Full revaluation, not Greeks approximation
          </div>
        </div>
      </div>

      <div className="bg-slate-800/50 rounded-xl border border-slate-700 p-4">
        <Plot
          data={[
            {
              type: "heatmap",
              x: data.spotShifts,
              y: data.volShifts,
              z: data.pnl,
              colorscale: [
                [0, "#ef4444"],
                [0.35, "#f87171"],
                [0.5, "#1e293b"],
                [0.65, "#4ade80"],
                [1, "#22c55e"],
              ],
              zmin: -maxAbs,
              zmax: maxAbs,
              colorbar: {
                title: { text: "P&L", font: { color: "#94a3b8", size: 12 } },
                tickfont: { color: "#94a3b8", size: 10 },
                tickprefix: "₹",
                len: 0.8,
              },
              hovertemplate:
                "Spot: %{x:+.1f}%<br>Vol: %{y:+.1f}%<br>P&L: ₹%{z:,.0f}<extra></extra>",
            },
          ]}
          layout={{
            autosize: true,
            height: 480,
            margin: { l: 60, r: 20, t: 20, b: 50 },
            paper_bgcolor: "transparent",
            plot_bgcolor: "transparent",
            xaxis: {
              title: { text: "Spot Shift (%)", font: { color: "#94a3b8" } },
              gridcolor: "#334155",
              color: "#94a3b8",
              ticksuffix: "%",
            },
            yaxis: {
              title: { text: "Vol Shift (%)", font: { color: "#94a3b8" } },
              gridcolor: "#334155",
              color: "#94a3b8",
              ticksuffix: "%",
            },
            font: { color: "#94a3b8" },
          }}
          config={{ responsive: true }}
          className="w-full"
        />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="bg-slate-800/30 rounded-lg border border-slate-700/50 p-4">
          <div className="text-sm font-medium text-emerald-400">Best case</div>
          <div className="text-2xl font-mono text-emerald-300 mt-1">
            +₹{Math.max(...data.pnl.flat()).toLocaleString()}
          </div>
          <div className="text-xs text-slate-500 mt-1">
            Spot up + vol up (long gamma, long vega)
          </div>
        </div>
        <div className="bg-slate-800/30 rounded-lg border border-slate-700/50 p-4">
          <div className="text-sm font-medium text-red-400">Worst case</div>
          <div className="text-2xl font-mono text-red-300 mt-1">
            -₹{Math.abs(Math.min(...data.pnl.flat())).toLocaleString()}
          </div>
          <div className="text-xs text-slate-500 mt-1">
            Spot down + vol down (double negative for long call)
          </div>
        </div>
      </div>
    </div>
  );
}
